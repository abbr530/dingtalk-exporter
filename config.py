import logging
import os
import re
import sys
from datetime import datetime
from log_utils import log_event

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

logger = logging.getLogger(__name__)

RUNTIME_DIAGNOSTICS = {
    "config_source": None,
    "env_override_uid": False,
    "env_override_data_dir": False,
    "partial_env_override": False,
    "redirect_file": None,
    "redirected_bases": [],
    "search_bases": [],
    "candidates": [],
    "selected_candidate": None,
    "selected_decrypt_uid_candidates": [],
    "candidate_decrypt_uid_candidates": {},
}


def _mask_uid(uid):
    uid = str(uid or "")
    if not uid or uid == "<YOUR_UID>":
        return uid
    if len(uid) <= 4:
        return uid
    return f"{'*' * (len(uid) - 4)}{uid[-4:]}"


def _iso_mtime(timestamp):
    return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")


def _dedupe_keep_order(values):
    result = []
    seen = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _masked_uid_list(values):
    return [_mask_uid(value) for value in values]


def _record_candidate(
    uid,
    path,
    mtime,
    version,
    uid_source="folder_name",
    log_matches=0,
    decrypt_uid_candidates=None,
    uid_source_log=None,
):
    normalized_path = os.path.normpath(path)
    decrypt_uid_candidates = _dedupe_keep_order(decrypt_uid_candidates or [uid])
    RUNTIME_DIAGNOSTICS["candidate_decrypt_uid_candidates"][normalized_path] = decrypt_uid_candidates
    RUNTIME_DIAGNOSTICS["candidates"].append(
        {
            "uid_masked": _mask_uid(uid),
            "path": normalized_path,
            "version": version,
            "uid_source": uid_source,
            "uid_source_log": uid_source_log,
            "log_matches": log_matches,
            "db_modified_at": _iso_mtime(mtime),
            "decrypt_uid_candidates_masked": _masked_uid_list(decrypt_uid_candidates),
        }
    )


def _get_candidate_info_by_path(path):
    normalized_path = os.path.normpath(path)
    for item in RUNTIME_DIAGNOSTICS["candidates"]:
        if item["path"] == normalized_path:
            return item
    return None


def _get_candidate_decrypt_uid_candidates(path, default_uid):
    normalized_path = os.path.normpath(path)
    return list(
        RUNTIME_DIAGNOSTICS["candidate_decrypt_uid_candidates"].get(
            normalized_path,
            [default_uid],
        )
    )


def _set_selected_candidate(
    uid,
    path,
    version,
    uid_source,
    decrypt_uid_candidates=None,
    uid_source_log=None,
    **extra,
):
    decrypt_uid_candidates = _dedupe_keep_order(decrypt_uid_candidates or [uid])
    RUNTIME_DIAGNOSTICS["selected_decrypt_uid_candidates"] = decrypt_uid_candidates
    selected = {
        "uid_masked": _mask_uid(uid),
        "path": os.path.normpath(path),
        "version": version,
        "uid_source": uid_source,
        "uid_source_log": uid_source_log,
        "decrypt_uid_candidates_masked": _masked_uid_list(decrypt_uid_candidates),
    }
    selected.update(extra)
    RUNTIME_DIAGNOSTICS["selected_candidate"] = selected


def _select_v3_log_names(log_dir):
    names = os.listdir(log_dir)
    cef_logs = sorted(name for name in names if name.startswith("cef_debug.log"))[-12:]
    gaea_logs = sorted(name for name in names if name.startswith("gaea.log"))
    selected = list(cef_logs)
    if gaea_logs:
        selected.append(gaea_logs[-1])
    return selected


def _load_redirected_dingtalk_bases():
    """Read any DingTalk app-data redirection targets configured by the client."""
    bases = []
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return bases

    redirect_file = os.path.join(appdata, "DingTalk", "redirectAppData.dat")
    RUNTIME_DIAGNOSTICS["redirect_file"] = redirect_file
    if not os.path.isfile(redirect_file):
        return bases

    try:
        with open(redirect_file, "r", encoding="utf-8") as f:
            redirected = f.read().strip().strip("\x00")
    except OSError as e:
        log_event(logger, "warning", "config.redirect_read_failed", path=redirect_file, error=e)
        return bases

    if redirected and os.path.isdir(redirected):
        redirected = os.path.normpath(redirected)
        bases.append(redirected)
        RUNTIME_DIAGNOSTICS["redirected_bases"].append(redirected)
        log_event(logger, "info", "config.redirect_detected", path=redirected)

    return bases


def _detect_v3_numeric_uid(data_dir, fallback_uid):
    """Try to resolve the real UID used by DingTalk V3 databases.

    V3 folder names are not always the same as the chat-database UID. In
    practice, the real UID is commonly surfaced in local client logs.
    """
    base_dir = os.path.dirname(os.path.normpath(data_dir))
    log_dir = os.path.join(base_dir, "log")
    if not os.path.isdir(log_dir):
        return fallback_uid, {
            "uid_source": "folder_name",
            "log_matches": 0,
            "decrypt_uid_candidates": [fallback_uid],
        }

    patterns = [
        re.compile(r"&uid=(\d{10})"),
        re.compile(r"real_uid=(\d{10})"),
        re.compile(r"myOpenId=(\d{10})"),
        re.compile(r"&cid=(\d{10}):"),
    ]
    scores = {}
    uid_log_scores = {}

    try:
        log_names = _select_v3_log_names(log_dir)
    except OSError as e:
        log_event(logger, "warning", "config.v3_log_scan_failed", path=log_dir, error=e)
        return fallback_uid, {
            "uid_source": "folder_name",
            "log_matches": 0,
            "decrypt_uid_candidates": [fallback_uid],
        }

    for name in log_names:
        path = os.path.join(log_dir, name)
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue

        score_uid = None
        if name.startswith("gaea.log"):
            real_uid_matches = patterns[1].findall(text)
            if real_uid_matches:
                real_uid_scores = {}
                for real_uid in real_uid_matches:
                    real_uid_scores[real_uid] = real_uid_scores.get(real_uid, 0) + 1
                score_uid = max(real_uid_scores.items(), key=lambda item: (item[1], item[0]))[0]

        for pattern in patterns:
            for match in pattern.finditer(text):
                uid = score_uid or match.group(1)
                scores[uid] = scores.get(uid, 0) + 1
                uid_log_scores.setdefault(uid, {})
                uid_log_scores[uid][name] = uid_log_scores[uid].get(name, 0) + 1

    if not scores:
        return fallback_uid, {
            "uid_source": "folder_name",
            "log_matches": 0,
            "decrypt_uid_candidates": [fallback_uid],
        }

    uid, match_count = max(scores.items(), key=lambda item: (item[1], item[0]))
    source_log = None
    if uid in uid_log_scores and uid_log_scores[uid]:
        source_log = max(uid_log_scores[uid].items(), key=lambda item: (item[1], item[0]))[0]
    decrypt_uid_candidates = _dedupe_keep_order([uid, fallback_uid])
    if uid != fallback_uid:
        log_event(
            logger,
            "info",
            "config.v3_uid_resolved",
            path=data_dir,
            uid_masked=_mask_uid(uid),
            fallback_uid_masked=_mask_uid(fallback_uid),
            matches=match_count,
            source_log=source_log,
            decrypt_uid_candidates_masked=_masked_uid_list(decrypt_uid_candidates),
        )
    return uid, {
        "uid_source": "log_scan",
        "uid_source_log": source_log,
        "log_matches": match_count,
        "decrypt_uid_candidates": decrypt_uid_candidates,
    }


def _detect_dingtalk_user():
    """Auto-detect DingTalk user data directory and UID.

    Scans multiple possible DingTalk data directories for *_v2 (or *_v3) folders.
    Returns (data_dir, uid) or (None, None) if not found.

    Override with environment variables:
      DINGTALK_UID       — your DingTalk user UID
      DINGTALK_DATA_DIR  — full path to the *_v2 directory
    """
    # Environment variables take highest priority
    env_uid = os.environ.get("DINGTALK_UID", "").strip()
    env_dir = os.environ.get("DINGTALK_DATA_DIR", "").strip()
    RUNTIME_DIAGNOSTICS["env_override_uid"] = bool(env_uid)
    RUNTIME_DIAGNOSTICS["env_override_data_dir"] = bool(env_dir)
    RUNTIME_DIAGNOSTICS["partial_env_override"] = bool(env_uid) ^ bool(env_dir)

    if env_uid and env_dir:
        log_event(
            logger,
            "info",
            "config.source_environment",
            uid_masked=_mask_uid(env_uid),
            path=env_dir,
        )
        RUNTIME_DIAGNOSTICS["config_source"] = "environment"
        _set_selected_candidate(
            uid=env_uid,
            path=env_dir,
            version="v3" if env_dir.endswith("_v3") else "v2",
            uid_source="environment",
            decrypt_uid_candidates=[env_uid],
        )
        return env_dir, env_uid
    if RUNTIME_DIAGNOSTICS["partial_env_override"]:
        log_event(
            logger,
            "warning",
            "config.partial_environment_override",
            env_uid_set=bool(env_uid),
            env_dir_set=bool(env_dir),
        )

    # Multiple possible DingTalk base directories (ordered by likelihood)
    search_bases = []
    appdata = os.environ.get("APPDATA", "")
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    userprofile = os.environ.get("USERPROFILE", "")

    if appdata:
        search_bases.append(os.path.join(appdata, "DingTalk"))
    if local_appdata and local_appdata != appdata:
        search_bases.append(os.path.join(local_appdata, "DingTalk"))
    if userprofile:
        search_bases.append(os.path.join(userprofile, "AppData", "Roaming", "DingTalk"))
        search_bases.append(os.path.join(userprofile, "AppData", "Local", "DingTalk"))
        search_bases.append(os.path.join(userprofile, "DingTalk"))
    search_bases.extend(_load_redirected_dingtalk_bases())

    # macOS and Linux paths
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        search_bases.append(os.path.join(home, "Library", "Application Support", "DingTalkMac"))
        search_bases.append(os.path.join(home, "Library", "Application Support", "DingTalk"))
    elif sys.platform.startswith("linux"):
        search_bases.append(os.path.join(home, ".config", "DingTalk"))
        search_bases.append(os.path.join(home, ".local", "share", "DingTalk"))

    # Deduplicate while preserving order
    seen = set()
    unique_bases = []
    for b in search_bases:
        b_norm = os.path.normcase(os.path.normpath(b))
        if b_norm not in seen:
            seen.add(b_norm)
            unique_bases.append(b)
    search_bases = unique_bases
    RUNTIME_DIAGNOSTICS["search_bases"] = [os.path.normpath(b) for b in search_bases]

    # Find all *_v2 / *_v3 user directories that have a database file
    v2_dirs = []
    for dingtalk_base in search_bases:
        if not os.path.isdir(dingtalk_base):
            continue
        for entry in os.listdir(dingtalk_base):
            if entry.endswith(("_v2", "_v3")):
                full_path = os.path.join(dingtalk_base, entry)
                if os.path.isdir(full_path):
                    db_file = os.path.join(full_path, "DBFiles", "dingtalk.db")
                    if os.path.exists(db_file):
                        uid = entry.rsplit("_v", 1)[0]
                        uid_meta = {
                            "uid_source": "folder_name",
                            "log_matches": 0,
                            "decrypt_uid_candidates": [uid],
                        }
                        if entry.endswith("_v3"):
                            uid, uid_meta = _detect_v3_numeric_uid(full_path, uid)
                        mtime = os.path.getmtime(db_file)
                        _record_candidate(
                            uid=uid,
                            path=full_path,
                            mtime=mtime,
                            version="v3" if entry.endswith("_v3") else "v2",
                            uid_source=uid_meta["uid_source"],
                            uid_source_log=uid_meta.get("uid_source_log"),
                            log_matches=uid_meta["log_matches"],
                            decrypt_uid_candidates=uid_meta["decrypt_uid_candidates"],
                        )
                        v2_dirs.append((uid, full_path, mtime))

    if not v2_dirs:
        return None, None

    if len(v2_dirs) == 1:
        uid, path, _ = v2_dirs[0]
        log_event(
            logger,
            "info",
            "config.auto_detected",
            uid_masked=_mask_uid(uid),
            path=path,
            candidates=len(v2_dirs),
        )
        RUNTIME_DIAGNOSTICS["config_source"] = "auto_detected"
        candidate_info = _get_candidate_info_by_path(path) or {}
        _set_selected_candidate(
            uid=uid,
            path=path,
            version="v3" if path.endswith("_v3") else "v2",
            uid_source=candidate_info.get("uid_source", "folder_name"),
            uid_source_log=candidate_info.get("uid_source_log"),
            decrypt_uid_candidates=_get_candidate_decrypt_uid_candidates(path, uid),
        )
        return path, uid

    # Multiple users: pick the one with most recently modified database
    v2_dirs.sort(key=lambda x: x[2], reverse=True)
    uid, path, _ = v2_dirs[0]
    all_uids = [f"  UID={u} (path={p})" for u, p, _ in v2_dirs]
    log_event(
        logger,
        "warning",
        "config.multiple_users_detected",
        selected_uid_masked=_mask_uid(uid),
        selected_path=path,
        candidates=[
            {"uid_masked": _mask_uid(u), "path": p}
            for u, p, _ in v2_dirs
        ],
    )
    RUNTIME_DIAGNOSTICS["config_source"] = "auto_detected"
    candidate_info = _get_candidate_info_by_path(path) or {}
    _set_selected_candidate(
        uid=uid,
        path=path,
        version="v3" if path.endswith("_v3") else "v2",
        uid_source=candidate_info.get("uid_source", "folder_name"),
        uid_source_log=candidate_info.get("uid_source_log"),
        decrypt_uid_candidates=_get_candidate_decrypt_uid_candidates(path, uid),
        selection_reason="latest_db_mtime",
    )
    return path, uid


def _detect_dingwave():
    """Auto-detect the dingwave binary in the tools/ directory.

    Checks multiple possible filenames to handle:
    - Platform differences (dingwave.exe vs dingwave)
    - Users who rename or download with different names
    """
    tools_dir = os.path.join(PROJECT_DIR, "tools")
    candidates = []

    if sys.platform == "win32":
        candidates = ["dingwave.exe", "dingwave"]
    else:
        candidates = ["dingwave", "dingwave.exe"]

    # Check exact names first
    for name in candidates:
        full = os.path.join(tools_dir, name)
        if os.path.isfile(full):
            return full

    # Fallback: find any executable-like file in tools/ with 'dingwave' in name
    if os.path.isdir(tools_dir):
        for f in os.listdir(tools_dir):
            lower = f.lower()
            if "dingwave" in lower and not lower.endswith((".md", ".txt", ".zip", ".tar", ".gz")):
                full = os.path.join(tools_dir, f)
                if os.path.isfile(full):
                    log_event(logger, "info", "config.dingwave_detected", path=full, filename=f)
                    return full

    # Return default (will fail later with clear message)
    return os.path.join(tools_dir, candidates[0])


# --- DingTalk data paths ---
# Auto-detection is tried first. If it fails (e.g. DingTalk not installed),
# set environment variables or edit the defaults below.
#
# Environment variables (recommended for override):
#   DINGTALK_UID       = your user UID (the number in the _v2 folder name)
#   DINGTALK_DATA_DIR  = full path to your DingTalk data directory
#
# Manual defaults (fallback when auto-detection and env vars both fail):
_detected_dir, _detected_uid = _detect_dingtalk_user()

DINGTALK_DATA_DIR = _detected_dir or r"C:\Users\<YOUR_USERNAME>\AppData\Roaming\DingTalk\<YOUR_UID>_v2"
USER_UID = _detected_uid or "<YOUR_UID>"

if not _detected_dir:
    RUNTIME_DIAGNOSTICS["config_source"] = "manual_fallback"
    _set_selected_candidate(
        uid=USER_UID,
        path=DINGTALK_DATA_DIR,
        version="v3" if DINGTALK_DATA_DIR.endswith("_v3") else "v2",
        uid_source="manual_default",
        decrypt_uid_candidates=[USER_UID],
    )

ENCRYPTED_DB_DIR = os.path.join(DINGTALK_DATA_DIR, "DBFiles")
ENCRYPTED_DB = os.path.join(ENCRYPTED_DB_DIR, "dingtalk.db")

# dingwave tool — auto-detected
DINGWAVE_PATH = _detect_dingwave()

# Sync settings
SYNC_INTERVAL_HOURS = 4
SYNC_OVERLAP_SECONDS = 24 *60
COPY_RETRY_COUNT = 3
COPY_RETRY_DELAY = 30  # seconds

# Data directories
DATA_DIR = os.path.join(PROJECT_DIR, "data")
DECRYPTED_DIR = os.path.join(DATA_DIR, "decrypted")
EXPORT_DIR = os.path.join(DATA_DIR, "exports")
LOGS_DIR = os.path.join(PROJECT_DIR, "logs")

# File paths
DECRYPTED_DB_PATH = os.path.join(DECRYPTED_DIR, "dingtalk.db")
SYNC_STATE_FILE = os.path.join(DATA_DIR, "sync_state.json")

# Attachment directories (relative to DINGTALK_DATA_DIR)
ATTACHMENT_DIRS = {
    "image": "ImageFiles",
    "audio": "AudioFiles",
    "video": "VideoFiles",
    "resource_cache": "resource_cache",
}

# Message content types
CONTENT_TYPE_TEXT = 1
CONTENT_TYPE_IMAGE = 2
CONTENT_TYPE_VOICE = 300
CONTENT_TYPE_FILE = 501
CONTENT_TYPE_RICH_TEXT = 1200
CONTENT_TYPE_INTERACTIVE_CARD = 2900
CONTENT_TYPE_MINI_APP_CARD = 2950
CONTENT_TYPE_QUOTE = 3100
CONTENT_TYPE_VIDEO_CALL = 1101
CONTENT_TYPE_APPROVAL = 1400

# Content type names for display
CONTENT_TYPE_NAMES = {
    1: "文本",
    2: "图片",
    3: "语音",
    4: "文件",
    102: "系统消息",
    104: "系统通知",
    202: "链接",
    300: "语音",
    400: "视频",
    500: "位置",
    501: "文件",
    503: "文件",
    1101: "通话",
    1200: "富文本",
    1201: "交互卡片",
    1202: "系统提示",
    1400: "审批",
    1500: "任务",
    1600: "日程",
    2900: "互动卡片",
    2950: "小程序卡片",
    3100: "引用消息",
}

# Web server settings
# WEB_HOST = "0.0.0.0"
WEB_HOST = "localhost"
WEB_PORT = 8090


def get_runtime_diagnostics():
    return {
        "config_source": RUNTIME_DIAGNOSTICS["config_source"],
        "env_override_uid": RUNTIME_DIAGNOSTICS["env_override_uid"],
        "env_override_data_dir": RUNTIME_DIAGNOSTICS["env_override_data_dir"],
        "partial_env_override": RUNTIME_DIAGNOSTICS["partial_env_override"],
        "redirect_file": RUNTIME_DIAGNOSTICS["redirect_file"],
        "redirected_bases": list(RUNTIME_DIAGNOSTICS["redirected_bases"]),
        "search_bases": list(RUNTIME_DIAGNOSTICS["search_bases"]),
        "candidate_count": len(RUNTIME_DIAGNOSTICS["candidates"]),
        "candidates": list(RUNTIME_DIAGNOSTICS["candidates"]),
        "selected_candidate": dict(RUNTIME_DIAGNOSTICS["selected_candidate"] or {}),
        "decrypt_uid_candidates_masked": _masked_uid_list(
            RUNTIME_DIAGNOSTICS["selected_decrypt_uid_candidates"]
        ),
        "dingtalk_data_dir": os.path.normpath(DINGTALK_DATA_DIR),
        "encrypted_db": os.path.normpath(ENCRYPTED_DB),
        "decrypted_db": os.path.normpath(DECRYPTED_DB_PATH),
        "user_uid_masked": _mask_uid(USER_UID),
        "is_v3": DINGTALK_DATA_DIR.endswith("_v3"),
    }


def get_decrypt_uid_candidates():
    return list(RUNTIME_DIAGNOSTICS["selected_decrypt_uid_candidates"] or [USER_UID])

# Ensure directories exist
for d in [DATA_DIR, DECRYPTED_DIR, EXPORT_DIR, LOGS_DIR]:
    os.makedirs(d, exist_ok=True)
