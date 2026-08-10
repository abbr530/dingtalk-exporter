import os
import shutil
import logging
import time
import tempfile
import base64
import hashlib
import json
import struct
import sqlite3

from Crypto.Cipher import AES

import config
from log_utils import log_event

logger = logging.getLogger(__name__)

PAGE_SIZE = 4096
WAL_HEADER_SIZE = 32
WAL_FRAME_HEADER_SIZE = 24
V3_PBKDF2_SALT = b"666DingTalk888"[:8]
V3_PBKDF2_ITERATIONS = 1000
V3_PBKDF2_KEYLEN = 32


def copy_encrypted_db(retry_count=None, retry_delay=None):
    """Copy encrypted database files to a temp directory to avoid lock conflicts."""
    if retry_count is None:
        retry_count = config.COPY_RETRY_COUNT
    if retry_delay is None:
        retry_delay = config.COPY_RETRY_DELAY

    temp_dir = tempfile.mkdtemp(prefix="dingtalk_encrypt_", dir=config.DECRYPTED_DIR)
    dest_db = os.path.join(temp_dir, "dingtalk_encrypted.db")

    if not os.path.isfile(config.ENCRYPTED_DB):
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise FileNotFoundError(
            f"未找到钉钉加密数据库: {config.ENCRYPTED_DB}\n"
            "请确认钉钉桌面客户端已登录，并检查自动检测到的数据目录是否正确。"
        )

    # Files to copy: main db + wal + shm
    files_to_copy = [
        (config.ENCRYPTED_DB, dest_db),
        (config.ENCRYPTED_DB + "-wal", dest_db + "-wal"),
        (config.ENCRYPTED_DB + "-shm", dest_db + "-shm"),
    ]

    for attempt in range(retry_count):
        try:
            copied_files = []
            for src, dst in files_to_copy:
                if os.path.exists(src):
                    shutil.copy2(src, dst)
                    copied_files.append(os.path.basename(src))

            if not os.path.isfile(dest_db):
                raise FileNotFoundError(
                    f"复制失败，未能从 {config.ENCRYPTED_DB} 生成临时数据库文件。"
                )

            log_event(
                logger,
                "info",
                "decrypt.copy_succeeded",
                temp_dir=temp_dir,
                files=copied_files,
            )
            return dest_db
        except (PermissionError, OSError) as e:
            log_event(
                logger,
                "warning",
                "decrypt.copy_retry_failed",
                attempt=attempt + 1,
                retry_count=retry_count,
                error=e,
            )
            if attempt < retry_count - 1:
                time.sleep(retry_delay)
            else:
                # Clean up partial copy
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise RuntimeError(
                    f"复制钉钉加密数据库失败，已重试 {retry_count} 次：{e}"
                )

    shutil.rmtree(temp_dir, ignore_errors=True)
    raise RuntimeError("复制钉钉加密数据库失败。")


def _generate_v2_key(user_uid):
    md5_hex = hashlib.md5(user_uid.encode("utf-8")).hexdigest()
    return md5_hex[:16].encode("ascii")


def _load_v3_salt(data_dir):
    config_path = os.path.join(data_dir, "user_config")
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"未找到钉钉 V3 user_config 文件: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        encoded = f.read().strip()

    try:
        payload = base64.b64decode(encoded).decode("utf-8")
        data = json.loads(payload)
    except Exception as e:  # pragma: no cover - depends on local file shape
        raise RuntimeError(f"解析钉钉 V3 user_config 失败: {e}") from e

    salt = (data.get("salt") or "").strip()
    if not salt:
        raise RuntimeError("钉钉 V3 user_config 中缺少 salt 字段。")
    log_event(
        logger,
        "info",
        "decrypt.v3_user_config_loaded",
        path=config_path,
        salt_len=len(salt),
        salt_prefix=salt[:6],
    )
    return salt


def _generate_v3_key(user_uid, data_dir, salt=None):
    if salt is None:
        salt = _load_v3_salt(data_dir)
    pbkdf2_output = hashlib.pbkdf2_hmac(
        "sha1",
        (user_uid + salt).encode("utf-8"),
        V3_PBKDF2_SALT,
        V3_PBKDF2_ITERATIONS,
        V3_PBKDF2_KEYLEN,
    )
    md5_hex = hashlib.md5(pbkdf2_output).hexdigest()
    return md5_hex[:16].encode("ascii")


def _build_database_keys():
    if config.DINGTALK_DATA_DIR.endswith("_v3"):
        uid_candidates = config.get_decrypt_uid_candidates()
        log_event(
            logger,
            "info",
            "decrypt.mode_selected",
            mode="v3",
            uid_masked=config.get_runtime_diagnostics()["user_uid_masked"],
            uid_candidates_masked=[config._mask_uid(uid) for uid in uid_candidates],
            data_dir=config.DINGTALK_DATA_DIR,
        )
        salt = _load_v3_salt(config.DINGTALK_DATA_DIR)
        return [
            (uid, _generate_v3_key(uid, config.DINGTALK_DATA_DIR, salt=salt))
            for uid in uid_candidates
        ]
    log_event(
        logger,
        "info",
        "decrypt.mode_selected",
        mode="v2",
        uid_masked=config.get_runtime_diagnostics()["user_uid_masked"],
        data_dir=config.DINGTALK_DATA_DIR,
    )
    return [(config.USER_UID, _generate_v2_key(config.USER_UID))]


def _decrypt_page_in_place(block, page):
    for offset in range(0, len(page), block.block_size):
        page[offset:offset + block.block_size] = block.decrypt(
            bytes(page[offset:offset + block.block_size])
        )


def _get_wal_page_size(wal_header):
    page_size = int.from_bytes(wal_header[8:12], "big")
    return 65536 if page_size == 1 else page_size


def _get_wal_checksum_byteorder(wal_header):
    magic = int.from_bytes(wal_header[:4], "big")
    if magic == 0x377F0682:
        return "little"
    if magic == 0x377F0683:
        return "big"
    raise RuntimeError(f"解密失败：无法识别 WAL magic number: 0x{magic:08x}")


def _update_wal_checksum(data, byteorder, s0=0, s1=0):
    if len(data) % 8 != 0:
        raise RuntimeError("解密失败：WAL 校验输入长度不是 8 的倍数。")

    for offset in range(0, len(data), 8):
        x0 = int.from_bytes(data[offset:offset + 4], byteorder)
        x1 = int.from_bytes(data[offset + 4:offset + 8], byteorder)
        s0 = (s0 + x0 + s1) & 0xFFFFFFFF
        s1 = (s1 + x1 + s0) & 0xFFFFFFFF
    return s0, s1


def _decrypt_database_pages(input_path, output_path, key):
    block = AES.new(key, AES.MODE_ECB)

    with open(input_path, "rb") as src, open(output_path, "wb") as dst:
        while True:
            page = bytearray(src.read(PAGE_SIZE))
            if not page:
                break

            if len(page) == PAGE_SIZE:
                _decrypt_page_in_place(block, page)

            dst.write(page)


def _decrypt_wal_pages(input_path, output_path, key):
    block = AES.new(key, AES.MODE_ECB)

    with open(input_path, "rb") as src, open(output_path, "wb") as dst:
        wal_header = src.read(WAL_HEADER_SIZE)
        if not wal_header:
            return 0
        if len(wal_header) != WAL_HEADER_SIZE:
            raise RuntimeError("解密失败：WAL 文件头长度异常。")
        dst.write(wal_header)

        page_size = _get_wal_page_size(wal_header)
        checksum_byteorder = _get_wal_checksum_byteorder(wal_header)
        checksum_1, checksum_2 = _update_wal_checksum(
            wal_header[:24], checksum_byteorder
        )

        frame_count = 0
        while True:
            frame_header = bytearray(src.read(WAL_FRAME_HEADER_SIZE))
            if not frame_header:
                break
            if len(frame_header) != WAL_FRAME_HEADER_SIZE:
                log_event(
                    logger,
                    "warning",
                    "decrypt.wal_truncated_frame_header",
                    expected=WAL_FRAME_HEADER_SIZE,
                    actual=len(frame_header),
                )
                break

            page = bytearray(src.read(page_size))
            if len(page) != page_size:
                log_event(
                    logger,
                    "warning",
                    "decrypt.wal_truncated_frame_page",
                    expected=page_size,
                    actual=len(page),
                    frame=frame_count,
                )
                break

            _decrypt_page_in_place(block, page)
            checksum_1, checksum_2 = _update_wal_checksum(
                bytes(frame_header[:8]) + bytes(page),
                checksum_byteorder,
                checksum_1,
                checksum_2,
            )
            frame_header[16:20] = struct.pack(">I", checksum_1)
            frame_header[20:24] = struct.pack(">I", checksum_2)
            dst.write(frame_header)
            dst.write(page)
            frame_count += 1

        return frame_count


def _remove_stale_decrypted_outputs(output_path):
    for path in [output_path, output_path + "-wal", output_path + "-shm"]:
        if os.path.exists(path):
            os.remove(path)


def _get_snapshot_latest_time(db_path):
    if not os.path.isfile(db_path):
        return 0

    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        table_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'tbmsg_%'"
        ).fetchall()
        latest = 0
        for (table_name,) in table_rows:
            row = conn.execute(f'SELECT MAX(createdAt) FROM "{table_name}"').fetchone()
            if row and row[0] and row[0] > latest:
                latest = row[0]
        return latest
    except sqlite3.Error as exc:
        log_event(
            logger,
            "warning",
            "decrypt.snapshot_inspect_failed",
            path=db_path,
            error=exc,
        )
        return 0
    finally:
        if conn is not None:
            conn.close()


def _read_export_latest_time(export_path):
    if not export_path:
        return 0

    json_path = os.path.join(export_path, "export.json")
    if not os.path.isfile(json_path):
        return 0

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log_event(
            logger,
            "warning",
            "decrypt.snapshot_export_read_failed",
            path=json_path,
            error=exc,
        )
        return 0

    latest = 0
    for conversation in payload.get("conversations", []):
        for message in conversation.get("messages", []):
            created_at = message.get("created_at") or 0
            if created_at > latest:
                latest = created_at
    return latest


def _get_known_latest_time():
    latest = 0
    if os.path.isfile(config.SYNC_STATE_FILE):
        try:
            with open(config.SYNC_STATE_FILE, "r", encoding="utf-8") as f:
                sync_state = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            log_event(
                logger,
                "warning",
                "decrypt.snapshot_state_read_failed",
                path=config.SYNC_STATE_FILE,
                error=exc,
            )
        else:
            latest = max(latest, _read_export_latest_time(sync_state.get("last_export_path")))
            cursor_time = int(sync_state.get("last_sync_time") or 0)
            if cursor_time:
                latest = max(latest, cursor_time + int(config.SYNC_OVERLAP_SECONDS) * 1000)

    latest = max(latest, _get_snapshot_latest_time(config.DECRYPTED_DB_PATH))
    return latest


def _promote_decrypted_snapshot(candidate_db_path, output_path):
    _remove_stale_decrypted_outputs(output_path)
    os.replace(candidate_db_path, output_path)

    candidate_wal_path = candidate_db_path + "-wal"
    if os.path.exists(candidate_wal_path):
        os.replace(candidate_wal_path, output_path + "-wal")


def _validate_snapshot_freshness(candidate_db_path):
    candidate_latest = _get_snapshot_latest_time(candidate_db_path)
    known_latest = _get_known_latest_time()
    if known_latest and candidate_latest and candidate_latest < known_latest:
        raise RuntimeError(
            f"解密结果回退：候选快照最新消息时间 {candidate_latest} 早于当前已知最新时间 {known_latest}。"
        )
    return candidate_latest, known_latest


def _can_use_existing_snapshot(output_path):
    return os.path.isfile(output_path) and _get_snapshot_latest_time(output_path) > 0


def _validate_decrypted_header(output_path):
    with open(output_path, "rb") as f:
        header = f.read(16)
    if header != b"SQLite format 3\x00":
        log_event(
            logger,
            "warning",
            "decrypt.header_mismatch",
            path=output_path,
            header_hex=header.hex(),
            header_ascii="".join(chr(b) if 32 <= b <= 126 else "." for b in header),
        )
        raise RuntimeError(
            "解密失败：输出文件不是有效的 SQLite 数据库。"
            "请确认自动识别到的 DingTalk UID 和数据目录是否正确。"
        )


def decrypt_database(encrypted_db_path=None, output_path=None):
    """Decrypt the copied DingTalk database into a plain SQLite file."""

    if encrypted_db_path is None:
        encrypted_db_path = copy_encrypted_db()
    if output_path is None:
        output_path = config.DECRYPTED_DB_PATH
    if not os.path.isfile(encrypted_db_path):
        raise FileNotFoundError(f"待解密数据库不存在: {encrypted_db_path}")

    log_event(
        logger,
        "info",
        "decrypt.started",
        source_db=encrypted_db_path,
        target_db=output_path,
        source_size_mb=round(os.path.getsize(encrypted_db_path) / 1024 / 1024, 1),
        mode="v3" if config.DINGTALK_DATA_DIR.endswith("_v3") else "v2",
    )

    try:
        key_candidates = _build_database_keys()
        last_error = None
        candidate_db_path = os.path.join(
            os.path.dirname(encrypted_db_path),
            "dingtalk_candidate.db",
        )
        _remove_stale_decrypted_outputs(candidate_db_path)
        for index, (key_uid, key) in enumerate(key_candidates):
            try:
                _decrypt_database_pages(encrypted_db_path, candidate_db_path, key)
                _validate_decrypted_header(candidate_db_path)

                encrypted_wal_path = encrypted_db_path + "-wal"
                if os.path.isfile(encrypted_wal_path):
                    wal_output_path = candidate_db_path + "-wal"
                    frame_count = _decrypt_wal_pages(encrypted_wal_path, wal_output_path, key)
                    log_event(
                        logger,
                        "info",
                        "decrypt.wal_completed",
                        wal_db=wal_output_path,
                        frame_count=frame_count,
                        wal_size_mb=round(os.path.getsize(wal_output_path) / 1024 / 1024, 1),
                    )

                candidate_latest, known_latest = _validate_snapshot_freshness(candidate_db_path)
                _promote_decrypted_snapshot(candidate_db_path, output_path)
                log_event(
                    logger,
                    "info",
                    "decrypt.snapshot_promoted",
                    output_db=output_path,
                    candidate_latest_time=candidate_latest,
                    known_latest_time=known_latest,
                )
                last_error = None
                break
            except RuntimeError as exc:
                last_error = exc
                _remove_stale_decrypted_outputs(candidate_db_path)
                if index >= len(key_candidates) - 1:
                    break
                log_event(
                    logger,
                    "warning",
                    "decrypt.uid_candidate_failed",
                    uid_masked=config._mask_uid(key_uid),
                    remaining_candidates=len(key_candidates) - index - 1,
                )
        if last_error:
            if _can_use_existing_snapshot(output_path):
                log_event(
                    logger,
                    "warning",
                    "decrypt.snapshot_fallback_used",
                    output_db=output_path,
                    error=last_error,
                )
                return output_path
            raise last_error

        output_size = os.path.getsize(output_path)
        if output_size <= 0:
            raise RuntimeError(
                "解密失败：输出的数据库文件为空。"
                "请确认自动识别到的 DingTalk 数据目录和 UID 是否正确。"
            )
        log_event(
            logger,
            "info",
            "decrypt.completed",
            output_db=output_path,
            output_size_mb=round(output_size / 1024 / 1024, 1),
        )

        return output_path

    finally:
        # Clean up the encrypted copy
        if encrypted_db_path and os.path.dirname(encrypted_db_path) != config.ENCRYPTED_DB_DIR:
            temp_dir = os.path.dirname(encrypted_db_path)
            shutil.rmtree(temp_dir, ignore_errors=True)
            log_event(logger, "info", "decrypt.temp_dir_cleaned", path=temp_dir)


def sync_decrypt():
    """Full sync: copy encrypted DB, decrypt, return path to decrypted DB."""
    log_event(logger, "info", "decrypt.sync_started")
    encrypted_copy = copy_encrypted_db()
    decrypted_path = decrypt_database(encrypted_copy)
    log_event(logger, "info", "decrypt.sync_completed", output_db=decrypted_path)
    return decrypted_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    path = sync_decrypt()
    print(f"解密后的数据库已生成：{path}")
