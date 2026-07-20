import json
import os
import sqlite3
import logging
import re
from urllib.parse import parse_qs, unquote, urlparse
from datetime import datetime
from typing import Optional

import config
from log_utils import log_event

logger = logging.getLogger(__name__)

# Message content type constants
MSG_TYPE_TEXT = 1
MSG_TYPE_IMAGE = 2
MSG_TYPE_AUDIO = 3
MSG_TYPE_VOICE = 300
MSG_TYPE_FILE = 501
MSG_TYPE_RICH_TEXT = 1200
MSG_TYPE_QUOTE = 3100
MSG_TYPE_APPROVAL = 1400
MSG_TYPE_VIDEO_CALL = 1101


class DatabaseNotReadyError(RuntimeError):
    """Raised when the decrypted DingTalk database is missing or unusable."""


def _invalid_database_error_message():
    return (
        "解密后的聊天数据库无效或已损坏，请重新点击“手动同步”完成一次新的解密。"
    )


def _get_db_file_info(db_path):
    info = {
        "path": db_path,
        "exists": os.path.isfile(db_path),
        "size": None,
        "header_hex": None,
        "header_ascii": None,
    }
    if not info["exists"]:
        return info

    try:
        info["size"] = os.path.getsize(db_path)
        with open(db_path, "rb") as f:
            header = f.read(16)
        info["header_hex"] = header.hex()
        info["header_ascii"] = "".join(chr(b) if 32 <= b <= 126 else "." for b in header)
    except OSError as exc:
        log_event(logger, "warning", "parser.db_inspect_failed", path=db_path, error=exc)
    return info


def _validate_database_schema(conn):
    """Ensure the decrypted database contains the tables this app requires."""
    table_names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }

    if "tbconversation" not in table_names:
        raise DatabaseNotReadyError(
            "解密后的聊天数据库尚未就绪，请先点击“手动同步”完成首次解密。"
        )

    if "tbuser_profile_v2" not in table_names:
        raise DatabaseNotReadyError(
            "解密后的聊天数据库缺少用户资料表，请重新执行一次手动同步。"
        )

    if not any(name.startswith("tbmsg_") for name in table_names):
        raise DatabaseNotReadyError(
            "解密后的聊天数据库缺少消息分表，请重新执行一次手动同步。"
        )


def get_connection(db_path=None):
    """Get a SQLite connection to the decrypted database."""
    if db_path is None:
        db_path = config.DECRYPTED_DB_PATH
    if not os.path.isfile(db_path):
        raise DatabaseNotReadyError(
            "未找到解密后的聊天数据库，请先点击“手动同步”完成首次解密。"
        )
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _validate_database_schema(conn)
    except sqlite3.Error as exc:
        if conn is not None:
            conn.close()
        log_event(logger, "warning", "parser.db_not_usable", path=db_path, error=exc)
        raise DatabaseNotReadyError(_invalid_database_error_message()) from exc
    except DatabaseNotReadyError:
        if conn is not None:
            conn.close()
        raise
    return conn


def get_database_status(db_path=None):
    """Return whether the decrypted database is ready for queries."""
    if db_path is None:
        db_path = config.DECRYPTED_DB_PATH
    info = _get_db_file_info(db_path)
    try:
        conn = get_connection(db_path)
    except DatabaseNotReadyError as exc:
        info["ready"] = False
        info["error"] = str(exc)
        return info
    else:
        conn.close()
        info["ready"] = True
        info["error"] = None
        return info


def get_conversations(conn, limit=100, offset=0, keyword=None):
    """Get conversation list with pagination and optional keyword filter."""
    sql = """
        SELECT cid, type, title, memberCount, createAt, lastModify,
               unreadCount, top, ownerId, isNotification, extension
        FROM tbconversation
        WHERE status = 1
    """
    params = []

    if keyword:
        sql += " AND title LIKE ?"
        params.append(f"%{keyword}%")

    # Count total
    count_sql = f"SELECT COUNT(*) FROM ({sql})"
    total = conn.execute(count_sql, params).fetchone()[0]

    # Order: top conversations first, then by lastModify
    sql += " ORDER BY top DESC, lastModify DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(sql, params).fetchall()

    conversations = []
    for row in rows:
        conv = {
            "cid": row["cid"],
            "type": "single" if row["type"] == 1 else "group",
            "title": row["title"] or "",
            "member_count": row["memberCount"],
            "create_at": row["createAt"],
            "last_modify": row["lastModify"],
            "unread_count": row["unreadCount"],
            "is_top": row["top"] > 0,
            "owner_id": row["ownerId"],
        }

        # For single chats, try to extract the other user's name from title
        if row["type"] == 1 and ":" in row["cid"]:
            # Single chat: cid format is "uid1:uid2"
            parts = row["cid"].split(":")
            other_uid = parts[1] if parts[0] == config.USER_UID else parts[0]
            conv["other_uid"] = other_uid
            # Try to get the other user's name
            user = get_user_profile(conn, int(other_uid))
            if user and user.get("nick"):
                conv["title"] = user["nick"]
            elif user and user.get("realName"):
                conv["title"] = user["realName"]

        conversations.append(conv)

    return {"total": total, "conversations": conversations}


def get_user_profile(conn, uid):
    """Get user profile by uid."""
    row = conn.execute(
        "SELECT uid, nick, realName, iconMediaId, mobile, email FROM tbuser_profile_v2 WHERE uid = ?",
        (uid,),
    ).fetchone()
    if row:
        return {
            "uid": row["uid"],
            "nick": row["nick"] or "",
            "real_name": row["realName"] or "",
            "avatar_id": row["iconMediaId"] or "",
            "mobile": row["mobile"] or "",
            "email": row["email"] or "",
        }
    return None


def get_all_user_profiles(conn):
    """Get all user profiles as a dict keyed by uid."""
    rows = conn.execute(
        "SELECT uid, nick, realName, iconMediaId FROM tbuser_profile_v2"
    ).fetchall()
    users = {}
    for row in rows:
        users[row["uid"]] = {
            "uid": row["uid"],
            "nick": row["nick"] or "",
            "real_name": row["realName"] or "",
        }
    return users


def _find_msg_table(conn, cid):
    """Find which sharded message table contains messages for the given cid."""
    for i in range(128):
        table = f"tbmsg_{i:03d}"
        try:
            row = conn.execute(
                f"SELECT 1 FROM \"{table}\" WHERE cid = ? LIMIT 1", (cid,)
            ).fetchone()
            if row:
                return table
        except sqlite3.OperationalError:
            continue
    return None


def _get_all_msg_tables():
    """Return list of all message table names."""
    return [f"tbmsg_{i:03d}" for i in range(128)]


def get_messages(conn, cid, limit=50, offset=0, since_time=None, until_time=None):
    """Get messages for a conversation with pagination and time filtering."""
    table = _find_msg_table(conn, cid)
    if not table:
        return {"total": 0, "messages": []}

    where = "WHERE cid = ? AND recallStatus = 0"
    params = [cid]

    if since_time:
        where += " AND createdAt > ?"
        params.append(since_time)
    if until_time:
        where += " AND createdAt <= ?"
        params.append(until_time)

    # Count
    count_sql = f'SELECT COUNT(*) FROM "{table}" {where}'
    total = conn.execute(count_sql, params).fetchone()[0]

    # Fetch messages ordered by time ASC (oldest first, newest at bottom)
    sql = f'''
        SELECT primaryKey, cid, localId, mid, senderId, type, creatorType,
               createdAt, lastModify, contentType, content, recallStatus,
               atIds, attachments, extension, readStatus, sentlocaltime
        FROM "{table}" {where}
        ORDER BY createdAt ASC
        LIMIT ? OFFSET ?
    '''
    # Reverse offset: show the LAST page by default (most recent messages)
    # offset=0 means "first page from end" = skip (total - limit) oldest messages
    actual_offset = max(0, total - limit - offset)
    params.extend([limit, actual_offset])

    rows = conn.execute(sql, params).fetchall()
    messages = [_parse_message(row, conn) for row in rows]
    _mark_ding_messages(messages)

    return {"total": total, "messages": messages}


def get_new_messages(conn, since_time, cid=None):
    """Get all new messages since a given timestamp across all tables (or for a specific conversation)."""
    messages = []

    if cid:
        tables = [_find_msg_table(conn, cid)] if _find_msg_table(conn, cid) else []
    else:
        tables = _get_all_msg_tables()

    for table in tables:
        try:
            sql = f'''
                SELECT primaryKey, cid, localId, mid, senderId, type, creatorType,
                       createdAt, lastModify, contentType, content, recallStatus,
                       atIds, attachments, extension, readStatus, sentlocaltime
                FROM "{table}"
                WHERE createdAt > ? AND recallStatus = 0
            '''
            params = [since_time]
            if cid:
                sql += " AND cid = ?"
                params.append(cid)

            rows = conn.execute(sql, params).fetchall()
            for row in rows:
                messages.append(_parse_message(row, conn))
        except sqlite3.OperationalError:
            continue

    # Sort by time
    messages.sort(key=lambda m: m["created_at"])
    _mark_ding_messages(messages)
    return messages


def _merge_inline_images(messages):
    """For messages containing [图片] text, find corresponding standalone image
    messages from the same sender within 60s and embed their URLs.

    Instead of removing image messages, we add an 'inline_images' list to the
    text message so the frontend can render images inline.
    """
    used = set()
    for i, msg in enumerate(messages):
        text = msg.get("text", "")
        if "[图片]" not in text:
            continue

        # Collect nearby image messages
        inline_imgs = []
        for j, other in enumerate(messages):
            if j == i or j in used:
                continue
            if (other.get("content_type") == 2
                    and other.get("sender_id") == msg.get("sender_id")
                    and abs(other.get("created_at", 0) - msg.get("created_at", 0)) < 60000):
                img_src = (other.get("image_info") or {}).get("src", "")
                if img_src:
                    inline_imgs.append(img_src)
                    used.add(j)

        if inline_imgs:
            msg["inline_images"] = inline_imgs


def _parse_message(row, conn):
    """Parse a message row into a structured dict."""
    content_type = row["contentType"]
    content_raw = row["content"] or ""
    attachments_raw = row["attachments"] or ""
    row_extension_data = _parse_json_dict(row["extension"])

    # Parse content JSON
    content_data = _parse_json_dict(content_raw)
    if not content_data and content_raw:
        content_data = {"text": content_raw}

    # Extract text content based on content type
    text = ""
    message_subtype = ""
    quote_info = None
    if content_type == MSG_TYPE_TEXT:
        text = content_data.get("text", "")
        if row_extension_data.get("BIType") == "confirm_ding_system_msg":
            message_subtype = "ding_status"
    elif content_type == MSG_TYPE_IMAGE:
        if _is_dingtalk_emoji(content_data):
            text = "[钉钉表情]"
            message_subtype = "emoji"
        else:
            text = "[图片]"
    elif content_type == MSG_TYPE_AUDIO:
        text = _extract_audio_text(content_data, row_extension_data)
        message_subtype = "voice"
    elif content_type == MSG_TYPE_VOICE:
        announcement_text = _extract_announcement_text(content_data)
        if announcement_text:
            text = announcement_text
            message_subtype = "announcement"
        else:
            report_text = _extract_report_text(content_data)
            if report_text:
                text = report_text
                message_subtype = "report"
            else:
                text = "[语音]"
                message_subtype = "voice"
    elif content_type in (102, 104):
        text = _extract_system_message_text(content_data, row_extension_data)
        if text.startswith("[公告]"):
            message_subtype = "announcement"
        elif row_extension_data.get("BIType") == "confirm_ding_system_msg" or "DING" in text:
            message_subtype = "ding_status"
    elif content_type == MSG_TYPE_FILE:
        text = "[文件]"
    elif content_type in (MSG_TYPE_RICH_TEXT, 1201, 1202):
        quote_info = _extract_reply_info(content_data, row_extension_data, conn)
        if quote_info:
            text = quote_info.get("reply_text", "")
            message_subtype = "reply"
        else:
            # Rich text / interactive buttons / system tips
            text = _extract_rich_text(content_data)
    elif content_type == MSG_TYPE_QUOTE:
        # Quote / re-edit messages
        text = _extract_quote_text(content_data)
    elif content_type in (2900, 2950):
        # Interactive cards / mini-app cards
        message_subtype = _detect_card_subtype(content_data, row_extension_data)
        if message_subtype == "report":
            text = _extract_report_card_text(conn, content_data, row_extension_data)
        else:
            text = _extract_card_text(content_data, row_extension_data)
    elif content_type == MSG_TYPE_APPROVAL:
        # Approval messages
        text = _extract_approval_text(content_data)
    elif content_type == MSG_TYPE_VIDEO_CALL:
        text = "[通话记录]"
    else:
        # Fallback: try to get text from content_data
        text = content_data.get("text", "")

    # Clean surrogate characters that can break JSON serialization
    if isinstance(text, str):
        text = _clean_surrogates(text)

    # Get sender info
    sender_id = row["senderId"]
    sender_name = ""
    user = get_user_profile(conn, sender_id) if sender_id else None
    if user:
        sender_name = user.get("real_name") or user.get("nick") or str(sender_id)

    # Parse attachments
    attachment_list = _parse_attachments(attachments_raw, content_data, content_type, row["mid"])

    # Parse @ mentions
    at_ids = {}
    try:
        if row["atIds"]:
            at_ids = json.loads(row["atIds"])
    except json.JSONDecodeError:
        pass

    msg = {
        "id": row["mid"],
        "cid": row["cid"],
        "sender_id": sender_id,
        "sender_name": _clean_surrogates(sender_name),
        "content_type": content_type,
        "content_type_name": config.CONTENT_TYPE_NAMES.get(content_type, f"未知({content_type})"),
        "message_subtype": message_subtype,
        "text": text,
        "created_at": row["createdAt"],
        "created_at_str": _format_timestamp(row["createdAt"]),
        "recall_status": row["recallStatus"],
        "at_ids": at_ids,
        "attachments": attachment_list,
        "is_ding": False,
    }

    if quote_info:
        msg["quote_info"] = quote_info

    # For images, add the local file path from im_image_info
    # Also for quote/rich-text messages that contain [图片] markers
    if content_type == MSG_TYPE_IMAGE:
        msg["image_info"] = _get_image_info(conn, row["cid"], row["mid"], content_data)
    elif "[图片]" in text:
        msg["image_info"] = _get_message_image_info(
            conn, row["cid"], row["mid"], content_data
        )
    if content_type == MSG_TYPE_AUDIO or (
        content_type == MSG_TYPE_VOICE and message_subtype == "voice"
    ):
        audio_info = _get_audio_info(content_data, row_extension_data)
        if audio_info:
            msg["audio_info"] = audio_info

    return msg


def _parse_attachments(attachments_raw, content_data, content_type, mid):
    """Parse attachment data from message."""
    import os as _os
    attachments = []

    # Build a lookup of content_data.attachments indexed by f_name for filepath info
    content_att_map = {}  # f_name -> filepath
    content_atts = content_data.get("attachments", [])
    if isinstance(content_atts, list):
        for ca in content_atts:
            ext = ca.get("extension", {})
            if isinstance(ext, str):
                try:
                    ext = json.loads(ext)
                except json.JSONDecodeError:
                    ext = {}
            fname = (ext.get("f_name", "") if isinstance(ext, dict) else "") or ca.get("filename", "")
            fp = ca.get("filepath", "")
            if fname and fp:
                content_att_map[fname] = fp

    # Try parsing the attachments field
    try:
        if attachments_raw:
            att_list = json.loads(attachments_raw)
            if isinstance(att_list, list):
                for att in att_list:
                    if isinstance(att, str):
                        att = json.loads(att)
                    att_type = att.get("type", 0)
                    ext = att.get("extension", {})
                    if isinstance(ext, str):
                        try:
                            ext = json.loads(ext)
                        except json.JSONDecodeError:
                            pass

                    a = {
                        "type": att_type,
                        "url": att.get("url", ""),
                        "size": att.get("size", 0),
                    }
                    # File attachments
                    if att_type == 501 or (isinstance(ext, dict) and ext.get("f_name")):
                        a["filename"] = ext.get("f_name", "") if isinstance(ext, dict) else ""
                        a["file_size"] = int(ext.get("f_size", 0)) if isinstance(ext, dict) else 0
                        a["file_type"] = ext.get("f_type", "") if isinstance(ext, dict) else ""
                        # Collect all candidate paths from different sources
                        candidates = []
                        if isinstance(ext, dict) and ext.get("path"):
                            candidates.append(ext["path"])
                        if att.get("filepath"):
                            candidates.append(att["filepath"])
                        if a["filename"] in content_att_map:
                            candidates.append(content_att_map[a["filename"]])
                        # Prefer the first path that actually exists locally
                        fpath = ""
                        for p in candidates:
                            if p and not p.startswith("\\\\") and _os.path.exists(p):
                                fpath = p
                                break
                        # If none exists, use first candidate for display
                        if not fpath and candidates:
                            fpath = candidates[0]
                        if fpath and not fpath.startswith("\\\\"):
                            a["local_available"] = _os.path.exists(fpath)
                            if a["local_available"]:
                                a["local_path"] = fpath
                        else:
                            a["local_available"] = False
                    attachments.append(a)
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: if attachments field was empty, use content_data directly
    if not attachments and isinstance(content_atts, list):
        for att in content_atts:
            att_type = att.get("type", 0)
            ext = att.get("extension", {})
            if isinstance(ext, str):
                try:
                    ext = json.loads(ext)
                except json.JSONDecodeError:
                    pass

            a = {
                "type": att_type,
                "url": att.get("url", ""),
                "size": att.get("size", 0),
            }
            if isinstance(ext, dict):
                if ext.get("f_name"):
                    a["filename"] = ext["f_name"]
                    a["file_size"] = int(ext.get("f_size", 0))
                    a["file_type"] = ext.get("f_type", "")
                    # Collect all candidate paths, prefer one that exists
                    candidates = [ext.get("path", ""), att.get("filepath", "")]
                    candidates = [p for p in candidates if p]
                    fpath = ""
                    for p in candidates:
                        if p and not p.startswith("\\\\") and _os.path.exists(p):
                            fpath = p
                            break
                    if not fpath and candidates:
                        fpath = candidates[0]
                    if fpath and not fpath.startswith("\\\\"):
                        a["local_available"] = _os.path.exists(fpath)
                        if a["local_available"]:
                            a["local_path"] = fpath
                    else:
                        a["local_available"] = False
                if ext.get("markdown"):
                    a["markdown"] = ext["markdown"][:500]
                if ext.get("desc"):
                    a["description"] = ext["desc"][:500]
            attachments.append(a)

    return attachments


def _parse_json_dict(raw):
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_dingtalk_emoji(content_data):
    ext = _parse_json_dict(content_data.get("extension"))
    url = content_data.get("url", "") or ""
    return bool(
        ext.get("e_id")
        or ext.get("p_id")
        or ext.get("pr_type")
        or url.lower().endswith(".gif")
    )


def _clean_card_title(text):
    if not text:
        return ""
    return re.sub(r"^\[[^\]]+\]\s*", "", text).strip()


def _extract_announcement_text(content_data):
    ext = _extract_ext_from_attachment(content_data)
    if not isinstance(ext, dict):
        return ""

    is_announcement = bool(
        ext.get("h_tl") == "公告"
        or (
            not ext.get("b_form")
            and (ext.get("b_tl") or ext.get("b_content"))
        )
    )
    if not is_announcement:
        return ""

    title = (ext.get("b_tl") or ext.get("title") or "").strip()
    content = (ext.get("b_content") or ext.get("desc") or "").strip()
    author = (ext.get("author") or "").strip()
    pieces = []
    if title:
        pieces.append(f"[公告] {title}")
    else:
        pieces.append("[公告]")
    if author:
        pieces.append(f"发布者：{author}")
    if content:
        pieces.append(content)
    return "\n".join(pieces).strip()


def _extract_audio_text(content_data, row_extension_data=None):
    row_extension_data = row_extension_data or {}
    for key in ("asrText", "audioToText", "audioText", "text"):
        value = row_extension_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    audio_content = content_data.get("audioContent", {}) or {}
    for key in ("text", "asrText", "audioToText"):
        value = audio_content.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "[语音消息]"


def _extract_report_text(content_data):
    ext = _extract_ext_from_attachment(content_data)
    if not isinstance(ext, dict) or not ext.get("b_form"):
        return ""
    title = (ext.get("b_tl") or ext.get("title") or "").strip()
    header = (ext.get("h_tl") or "日志").strip()
    form_text = _format_report_form(ext.get("b_form"))
    pieces = []
    pieces.append(f"[{header}] {title}".strip())
    if form_text:
        pieces.append(form_text)
    return "\n".join(piece for piece in pieces if piece).strip()


def _format_report_form(raw_form):
    try:
        items = json.loads(raw_form) if isinstance(raw_form, str) else raw_form
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(items, list):
        return ""

    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("k", "")).strip()
        value = str(item.get("v", "")).replace("\r\n", "\n").replace("\r", "\n").strip()
        if not key and not value:
            continue
        if value:
            lines.append(f"{key}\n{value}" if key else value)
        else:
            lines.append(key)
    return "\n\n".join(lines).strip()


def _extract_system_message_text(content_data, row_extension_data=None):
    row_extension_data = row_extension_data or {}
    announcement_text = _extract_announcement_text(content_data)
    if announcement_text:
        return announcement_text

    ext = _extract_ext_from_attachment(content_data)
    pieces = []

    def add_piece(value):
        value = (value or "").strip()
        if not value:
            return
        if value in pieces:
            return
        pieces.append(value)

    add_piece(_clean_card_title(row_extension_data.get("biz_custom_title", "")))
    add_piece(row_extension_data.get("biz_custom_desc", ""))
    add_piece(ext.get("title", ""))
    add_piece(ext.get("text", ""))
    add_piece(ext.get("desc", ""))
    add_piece(ext.get("searchDesc", ""))
    add_piece(ext.get("interactiveCardLastMessage", ""))
    add_piece(content_data.get("text", ""))

    return "\n".join(pieces).strip()


def _get_image_info(conn, cid, mid, content_data):
    """Get image file info from im_image_info table and content data.

    Returns a dict with single image info (for backward compat) plus
    an 'images' list for messages with multiple images (e.g. quote/rich text).
    """
    import os as _os
    info = {"file_size": 0, "url": "", "cached": False, "local_path": "", "images": []}

    # Query ALL matching rows (a message may have multiple images)
    rows = conn.execute(
        "SELECT url, local_path, size FROM im_image_info WHERE cid = ? AND mid = ?",
        (cid, mid),
    ).fetchall()

    for row in rows:
        local_path = row["local_path"] or ""
        if local_path and _os.path.exists(local_path) and _os.path.getsize(local_path) > 500:
            img = {
                "file_size": _os.path.getsize(local_path),
                "url": row["url"] or "",
                "cached": True,
                "local_path": local_path,
                "src": _local_path_to_url(local_path),
            }
            info["images"].append(img)

    # Set primary info from first valid image (backward compat)
    if info["images"]:
        primary = info["images"][0]
        info["file_size"] = primary["file_size"]
        info["url"] = primary["url"]
        info["cached"] = True
        info["local_path"] = primary["local_path"]
        info["src"] = primary["src"]
        return info

    # Fallback: check blurredPath in content (ImageFiles directory)
    blurred = content_data.get("blurredPath", "")
    if blurred and _os.path.exists(blurred) and _os.path.getsize(blurred) > 500:
        img = {
            "file_size": _os.path.getsize(blurred),
            "url": "",
            "cached": True,
            "local_path": blurred,
            "src": _local_path_to_url(blurred),
        }
        info["images"].append(img)
        info["file_size"] = img["file_size"]
        info["cached"] = True
        info["local_path"] = blurred
        info["src"] = img["src"]
        return info

    # Final fallback: use the remote URL directly (useful for DingTalk emoji GIFs).
    remote_url = content_data.get("url", "") or ""
    if remote_url.startswith(("http://", "https://")):
        img = {
            "file_size": 0,
            "url": remote_url,
            "cached": False,
            "local_path": "",
            "src": remote_url,
        }
        info["images"].append(img)
        info["url"] = remote_url
        info["src"] = remote_url

    return info


def _get_message_image_info(conn, cid, mid, content_data):
    info = _get_image_info(conn, cid, mid, content_data)
    embedded_images = _extract_embedded_images(conn, cid, mid, content_data)
    if not embedded_images:
        return info

    existing_srcs = {
        item.get("src")
        for item in info.get("images", [])
        if isinstance(item, dict) and item.get("src")
    }
    for image in embedded_images:
        if image.get("src") and image.get("src") not in existing_srcs:
            info.setdefault("images", []).append(image)
            existing_srcs.add(image.get("src"))

    if info.get("images") and not info.get("src"):
        primary = info["images"][0]
        info["file_size"] = primary.get("file_size", 0)
        info["url"] = primary.get("url", "")
        info["cached"] = bool(primary.get("cached"))
        info["local_path"] = primary.get("local_path", "")
        info["src"] = primary.get("src", "")

    return info


def _extract_embedded_images(conn, cid, mid, content_data):
    ext = _extract_ext_from_attachment(content_data)
    images = []
    payload_v2 = _parse_json_dict(ext.get("payloadV2"))
    contents = payload_v2.get("contents", [])
    if isinstance(contents, list):
        for content in contents:
            text_block = (content or {}).get("text", {})
            items = text_block.get("items", []) if isinstance(text_block, dict) else []
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and item.get("type") == "image":
                    image = _resolve_payload_image(conn, cid, mid, item.get("data", {}))
                    if image:
                        images.append(image)

    if images:
        return images

    payload = _parse_json_dict(ext.get("payload"))
    items = payload.get("items", [])
    if not isinstance(items, list):
        return []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "img":
            continue
        value = item.get("value", {}) or {}
        data = {"url": value.get("src", "")}
        image = _resolve_payload_image(conn, cid, mid, data)
        if image:
            images.append(image)
    return images


def _resolve_payload_image(conn, cid, mid, data):
    if not isinstance(data, dict):
        return None
    auth_media_id = (data.get("authMediaId") or "").lstrip("$").strip()
    media_id = (data.get("url") or data.get("src") or "").replace("mediaId://", "").strip()

    rows = []
    if auth_media_id:
        rows = conn.execute(
            "SELECT url, local_path, size FROM im_image_info WHERE cid = ? AND url LIKE ?",
            (cid, f"%{auth_media_id}%"),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                "SELECT url, local_path, size FROM im_image_info WHERE url LIKE ? LIMIT 5",
                (f"%{auth_media_id}%",),
            ).fetchall()

    if not rows and media_id:
        related = _find_image_rows_by_media_id(conn, media_id)
        if related:
            rows = related

    if rows:
        row = rows[0]
        local_path = row["local_path"] or ""
        src = _local_path_to_url(local_path) if local_path and os.path.exists(local_path) else ""
        url = row["url"] or ""
        if not src and url.startswith(("http://", "https://")):
            src = url
        if src:
            return {
                "file_size": os.path.getsize(local_path) if local_path and os.path.exists(local_path) else 0,
                "url": url,
                "cached": bool(local_path and os.path.exists(local_path)),
                "local_path": local_path,
                "src": src,
            }
    return None


def _find_image_rows_by_media_id(conn, media_id):
    if not media_id:
        return []
    for table in _get_all_msg_tables():
        try:
            rows = conn.execute(
                f'''SELECT cid, mid FROM "{table}" WHERE contentType = 2 AND content LIKE ? LIMIT 5''',
                (f"%{media_id}%",),
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            image_rows = conn.execute(
                "SELECT url, local_path, size FROM im_image_info WHERE cid = ? AND mid = ?",
                (row["cid"], row["mid"]),
            ).fetchall()
            if image_rows:
                return image_rows
    return []


def _local_path_to_url(local_path):
    """Convert a local DingTalk file path to a browser-accessible URL."""
    import os as _os
    # Extract the relative part after the DingTalk data dir
    data_dir = config.DINGTALK_DATA_DIR.rstrip(_os.sep) + _os.sep
    if local_path.startswith(data_dir):
        rel = local_path[len(data_dir):]
        return "/api/attachments/" + rel.replace("\\", "/")
    return ""


def _get_audio_info(content_data, row_extension_data=None):
    row_extension_data = row_extension_data or {}
    audio_content = content_data.get("audioContent", {}) or {}
    media_id = (audio_content.get("mediaId") or "").lstrip("@")
    filepath = audio_content.get("filepath", "") or ""
    duration_ms = _safe_int(audio_content.get("duration"))
    transcript = ""
    for key in ("asrText", "audioToText", "audioText", "text"):
        value = row_extension_data.get(key)
        if isinstance(value, str) and value.strip():
            transcript = value.strip()
            break

    local_path = ""
    candidates = []
    if filepath:
        candidates.append(filepath)
    if media_id:
        audio_dir = os.path.join(config.DINGTALK_DATA_DIR, "AudioFiles")
        for ext in (".ogg", ".amr", ".wav", ".mp3", ".m4a"):
            candidates.append(os.path.join(audio_dir, f"{media_id}{ext}"))
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            local_path = candidate
            break

    src = _local_path_to_url(local_path) if local_path else ""
    if not src:
        auth_media_id = (row_extension_data.get("authMediaId") or "").strip()
        if auth_media_id:
            src = ""

    if not local_path and not transcript:
        return {
            "duration_ms": duration_ms,
            "src": "",
            "local_path": "",
            "transcript": "",
            "file_name": "",
        }

    return {
        "duration_ms": duration_ms,
        "src": src,
        "local_path": local_path,
        "transcript": transcript,
        "file_name": os.path.basename(local_path) if local_path else "",
    }


def _mark_ding_messages(messages):
    for idx, msg in enumerate(messages):
        if msg.get("message_subtype") != "ding_status":
            continue
        if "你的DING" not in (msg.get("text") or ""):
            continue
        candidates = []
        for prev in range(idx - 1, -1, -1):
            candidate = messages[prev]
            if candidate.get("cid") != msg.get("cid"):
                continue
            if msg.get("created_at", 0) - candidate.get("created_at", 0) > 15 * 60 * 1000:
                break
            if str(candidate.get("sender_id")) != str(config.USER_UID):
                continue
            if candidate.get("message_subtype") == "ding_status":
                continue
            candidates.append(candidate)
        if candidates:
            candidates[-1]["is_ding"] = True


def search_messages(conn, keyword, limit=50, offset=0):
    """Search messages by keyword across all message tables."""
    results = []
    for table in _get_all_msg_tables():
        try:
            rows = conn.execute(f'''
                SELECT primaryKey, cid, localId, mid, senderId, type, creatorType,
                       createdAt, lastModify, contentType, content, recallStatus,
                       atIds, attachments, extension, readStatus, sentlocaltime
                FROM "{table}"
                WHERE content LIKE ? AND recallStatus = 0
                ORDER BY createdAt DESC
                LIMIT ? OFFSET ?
            ''', (f"%{keyword}%", limit, offset)).fetchall()

            for row in rows:
                results.append(_parse_message(row, conn))
        except sqlite3.OperationalError:
            continue

    return results


def get_conversation_stats(conn):
    """Get overall statistics."""
    stats = {
        "total_conversations": 0,
        "total_messages": 0,
        "single_chats": 0,
        "group_chats": 0,
        "total_users": 0,
    }

    row = conn.execute("SELECT COUNT(*), SUM(CASE WHEN type=1 THEN 1 ELSE 0 END), SUM(CASE WHEN type=2 THEN 1 ELSE 0 END) FROM tbconversation WHERE status=1").fetchone()
    stats["total_conversations"] = row[0]
    stats["single_chats"] = row[1] or 0
    stats["group_chats"] = row[2] or 0

    for table in _get_all_msg_tables():
        try:
            count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            stats["total_messages"] += count
        except sqlite3.OperationalError:
            continue

    stats["total_users"] = conn.execute("SELECT COUNT(*) FROM tbuser_profile_v2").fetchone()[0]

    return stats


def get_latest_message_time(conn):
    """Get the timestamp of the latest message in the database."""
    latest = 0
    for table in _get_all_msg_tables():
        try:
            row = conn.execute(f'SELECT MAX(createdAt) FROM "{table}"').fetchone()
            if row and row[0] and row[0] > latest:
                latest = row[0]
        except sqlite3.OperationalError:
            continue
    return latest


def _format_timestamp(ts):
    """Format a millisecond timestamp to ISO string."""
    if not ts:
        return ""
    try:
        dt = datetime.fromtimestamp(ts / 1000)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return str(ts)


def _extract_ext_from_attachment(content_data):
    """Extract extension dict from the first attachment in content_data."""
    atts = content_data.get("attachments", [])
    if not atts or not isinstance(atts, list):
        return {}
    att = atts[0]
    ext = att.get("extension", {})
    if isinstance(ext, str):
        try:
            ext = json.loads(ext)
        except json.JSONDecodeError:
            ext = {}
    return ext


def _extract_rich_text(content_data):
    """Extract text from rich text / markdown messages (contentType=1200/1201/1202)."""
    ext = _extract_ext_from_attachment(content_data)
    # Priority: markdown > title > desc
    if ext.get("markdown"):
        md = ext["markdown"]
        # Strip the sender prefix line (e.g. "> ###### 蒋剑平(蒋剑平)\n")
        lines = md.split("\n")
        cleaned = []
        for line in lines:
            if line.startswith("> ###### "):
                continue  # skip sender name line
            if line.startswith("---"):
                continue  # skip separator
            cleaned.append(line)
        return "\n".join(cleaned).strip()
    if ext.get("title"):
        return ext["title"]
    if ext.get("desc"):
        return ext["desc"]
    return ""


def _extract_reply_info(content_data, row_extension_data, conn):
    attachments = content_data.get("attachments", [])
    if not isinstance(attachments, list):
        return None

    reply_ext = {}
    markdown_ext = {}
    for att in attachments:
        if not isinstance(att, dict):
            continue
        ext = att.get("extension", {})
        if isinstance(ext, str):
            try:
                ext = json.loads(ext)
            except json.JSONDecodeError:
                ext = {}
        if not isinstance(ext, dict):
            continue
        if ext.get("sourceMessageModel") or ext.get("replyContent"):
            reply_ext = ext
        if ext.get("markdown"):
            markdown_ext = ext

    if not reply_ext and not markdown_ext:
        return None

    source_sender_name = ""
    source_sender_id = row_extension_data.get("sourceSenderId")
    if source_sender_id:
        source_user = get_user_profile(conn, int(source_sender_id))
        if source_user:
            source_sender_name = (
                source_user.get("real_name") or source_user.get("nick") or str(source_sender_id)
            )
    if not source_sender_name:
        source_sender_name = _extract_markdown_source_sender(markdown_ext.get("markdown", ""))

    reply_text = (reply_ext.get("replyContent") or markdown_ext.get("title") or "").strip()
    source_preview = ""
    source_model = _parse_json_dict(reply_ext.get("sourceMessageModel"))
    if source_model:
        source_preview = _render_source_message_text(source_model)
    if not source_preview:
        source_preview = _extract_markdown_source_preview(markdown_ext.get("markdown", ""))

    return {
        "source_sender_name": source_sender_name,
        "source_preview": source_preview,
        "reply_text": reply_text,
    }


def _extract_markdown_source_sender(markdown):
    if not markdown:
        return ""
    match = re.search(r"^> ######\s+(.+)$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _extract_markdown_source_preview(markdown):
    if not markdown:
        return ""
    lines = markdown.splitlines()
    preview_lines = []
    in_reply = False
    for line in lines:
        if line.startswith("---------------"):
            in_reply = True
            continue
        if in_reply:
            continue
        if line.startswith("> ###### "):
            continue
        if line.startswith("> "):
            preview_lines.append(line[2:].strip())
    return "\n".join(line for line in preview_lines if line).strip()


def _render_source_message_text(source_model):
    content = source_model.get("content", {}) or {}
    content_type = content.get("contentType")
    if content_type == 1:
        text_content = content.get("textContent", {}) or {}
        text = text_content.get("text") or content.get("text") or ""
        at_open_ids = content.get("atOpenIds", {}) or {}
        for uid, name in at_open_ids.items():
            text = text.replace(f"@{uid}", f"@{name}")
        return text.strip()
    if content_type == 2:
        return "[图片]"
    if content_type == 3:
        return "[语音消息]"
    return ""


def _extract_quote_text(content_data):
    """Extract text from quote/re-edit messages (contentType=3100)."""
    ext = _extract_ext_from_attachment(content_data)
    payload_v2_text = _extract_dynamic_payload_v2_text(ext.get("payloadV2"))
    if payload_v2_text:
        return payload_v2_text
    payload_text = _extract_dynamic_payload_text(ext.get("payload"))
    if payload_text:
        return payload_text
    if ext.get("desc"):
        return ext["desc"]
    if ext.get("title"):
        return ext["title"]
    # Fallback: try content_data.text
    return content_data.get("text", "")


def _extract_dynamic_payload_v2_text(raw_payload):
    payload = _parse_json_dict(raw_payload)
    contents = payload.get("contents", [])
    if not isinstance(contents, list):
        return ""

    pieces = []
    for content in contents:
        if not isinstance(content, dict):
            continue
        text_block = content.get("text", {})
        if not isinstance(text_block, dict):
            continue
        items = text_block.get("items", [])
        if not isinstance(items, list):
            continue
        block_parts = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            data = item.get("data", {}) or {}
            if item_type == "newLine":
                block_parts.append("\n")
            elif item_type == "image":
                block_parts.append("[图片]")
            elif item_type in ("text", "link"):
                text = data.get("text", "")
                if text:
                    block_parts.append(str(text))
        block_text = "".join(block_parts).strip()
        if block_text:
            pieces.append(block_text)
    return "\n".join(pieces).strip()


def _extract_dynamic_payload_text(raw_payload):
    payload = _parse_json_dict(raw_payload)
    items = payload.get("items", [])
    if not isinstance(items, list):
        return ""

    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        value = item.get("value", {}) or {}
        if item_type == "img":
            lines.append("[图片]")
            continue
        if item_type != "rt":
            continue
        text_runs = value.get("textRuns", [])
        if not isinstance(text_runs, list):
            continue
        text = "".join(str(run.get("text", "")) for run in text_runs if isinstance(run, dict))
        lines.append(text)
    return "\n".join(lines).strip()


def _extract_report_card_text(conn, content_data, row_extension_data):
    base_text = _extract_card_text(content_data, row_extension_data)
    report_id = _extract_report_id(row_extension_data)
    if not report_id:
        return base_text
    full_text = _lookup_report_text_by_report_id(conn, report_id)
    if full_text:
        return full_text
    return base_text


def _extract_report_id(row_extension_data):
    report_id = (row_extension_data.get("reportId") or "").strip()
    if report_id:
        return report_id
    action_url = row_extension_data.get("biz_custom_action_url", "") or row_extension_data.get("apn_nav_url", "")
    if not action_url:
        return ""
    decoded = unquote(action_url)
    parsed = urlparse(decoded)
    params = parse_qs(parsed.query)
    if "id" in params and params["id"]:
        return params["id"][0]
    if "url=" in decoded:
        nested = decoded.split("url=", 1)[1].split("&", 1)[0]
        nested = unquote(nested)
        nested_parsed = urlparse(nested)
        nested_params = parse_qs(nested_parsed.query)
        if "id" in nested_params and nested_params["id"]:
            return nested_params["id"][0]
    return ""


def _lookup_report_text_by_report_id(conn, report_id):
    for table in _get_all_msg_tables():
        try:
            row = conn.execute(
                f'''SELECT content FROM "{table}" WHERE content LIKE ? LIMIT 1''',
                (f"%{report_id}%",),
            ).fetchone()
        except sqlite3.OperationalError:
            continue
        if not row:
            continue
        content_data = _parse_json_dict(row["content"])
        report_text = _extract_report_text(content_data)
        if report_text:
            return report_text
    return ""


def _extract_card_text(content_data, row_extension_data=None):
    """Extract text from interactive card / mini-app card messages (contentType=2900/2950)."""
    row_extension_data = row_extension_data or {}
    ext = _extract_ext_from_attachment(content_data)
    biz_title = _clean_card_title(row_extension_data.get("biz_custom_title", ""))
    biz_desc = row_extension_data.get("biz_custom_desc", "")
    if biz_title:
        pieces = [biz_title]
        if biz_desc:
            pieces.append(biz_desc.strip())
        return "\n".join(piece for piece in pieces if piece).strip()
    # Priority: searchDesc > LastMessageI18n > interactiveCardLastMessage > title
    if ext.get("searchDesc"):
        return ext["searchDesc"]
    # Try to parse LastMessageI18n
    last_msg_i18n = ext.get("LastMessageI18n", "")
    if last_msg_i18n:
        try:
            i18n = json.loads(last_msg_i18n) if isinstance(last_msg_i18n, str) else last_msg_i18n
            text = i18n.get("zh_CN", "")
            if text:
                return text
        except json.JSONDecodeError:
            pass
    if ext.get("interactiveCardLastMessage"):
        return ext["interactiveCardLastMessage"]
    if ext.get("title"):
        return ext["title"]
    return ""


def _detect_card_subtype(content_data, row_extension_data):
    if row_extension_data.get("BIType") == "group_plug_vote":
        return "vote"
    ext = _extract_ext_from_attachment(content_data)
    last_message = (
        row_extension_data.get("interactiveCardLastMessage")
        or ext.get("interactiveCardLastMessage")
        or ""
    )
    combined_text = " ".join(
        str(value)
        for value in (
            row_extension_data.get("msgSrcBizId", ""),
            row_extension_data.get("biz_custom_action_name", ""),
            row_extension_data.get("biz_custom_title", ""),
            last_message,
        )
        if value
    )
    if "投票" in last_message:
        return "vote"
    if any(keyword in combined_text for keyword in ("日志", "日报", "周报", "月报")):
        return "report"
    if row_extension_data.get("reportId") or row_extension_data.get("lippiReport"):
        return "report"
    return "card"


def _extract_approval_text(content_data):
    """Extract text from approval messages (contentType=1400)."""
    ext = _extract_ext_from_attachment(content_data)
    if ext.get("markdown"):
        return ext["markdown"]
    if ext.get("title"):
        return ext["title"]
    return ""


def _clean_surrogates(s):
    """Remove or replace UTF-16 surrogate characters that break JSON serialization."""
    if not isinstance(s, str):
        return s
    # Replace any surrogate characters with empty string
    result = []
    for ch in s:
        code = ord(ch)
        if 0xD800 <= code <= 0xDFFF:
            continue  # skip surrogate
        result.append(ch)
    return ''.join(result)


if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    logging.basicConfig(level=logging.INFO)

    conn = get_connection()
    stats = get_conversation_stats(conn)
    print(f"统计信息：{json.dumps(stats, ensure_ascii=False, indent=2)}")

    convs = get_conversations(conn, limit=5)
    print("\n最近的 5 个会话：")
    for c in convs["conversations"]:
        print(f"  [{c['type']}] {c['title']}（成员数：{c['member_count']}，最后更新时间：{c.get('last_modify', 0)}）")

    if convs["conversations"]:
        cid = convs["conversations"][0]["cid"]
        msgs = get_messages(conn, cid, limit=3)
        print(f"\n会话“{convs['conversations'][0]['title']}”的最近 3 条消息：")
        for m in msgs["messages"]:
            print(f"  [{m['created_at_str']}] {m['sender_name']}: {m['text'][:100]}")

    conn.close()
