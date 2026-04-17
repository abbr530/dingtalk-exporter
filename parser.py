import json
import sqlite3
import logging
from datetime import datetime
from typing import Optional

import config

logger = logging.getLogger(__name__)

# Message content type constants
MSG_TYPE_TEXT = 1
MSG_TYPE_IMAGE = 2
MSG_TYPE_VOICE = 300
MSG_TYPE_FILE = 501
MSG_TYPE_RICH_TEXT = 1200
MSG_TYPE_QUOTE = 3100
MSG_TYPE_APPROVAL = 1400
MSG_TYPE_VIDEO_CALL = 1101


def get_connection(db_path=None):
    """Get a SQLite connection to the decrypted database."""
    if db_path is None:
        db_path = config.DECRYPTED_DB_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


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

    # Parse content JSON
    content_data = {}
    try:
        content_data = json.loads(content_raw) if content_raw else {}
    except json.JSONDecodeError:
        content_data = {"text": content_raw}

    # Extract text content based on content type
    text = ""
    if content_type == MSG_TYPE_TEXT:
        text = content_data.get("text", "")
    elif content_type == MSG_TYPE_IMAGE:
        text = "[图片]"
    elif content_type == MSG_TYPE_VOICE:
        text = "[语音]"
    elif content_type == MSG_TYPE_FILE:
        text = "[文件]"
    elif content_type in (MSG_TYPE_RICH_TEXT, 1201, 1202):
        # Rich text / interactive buttons / system tips
        text = _extract_rich_text(content_data)
    elif content_type == MSG_TYPE_QUOTE:
        # Quote / re-edit messages
        text = _extract_quote_text(content_data)
    elif content_type in (2900, 2950):
        # Interactive cards / mini-app cards
        text = _extract_card_text(content_data)
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
        "text": text,
        "created_at": row["createdAt"],
        "created_at_str": _format_timestamp(row["createdAt"]),
        "recall_status": row["recallStatus"],
        "at_ids": at_ids,
        "attachments": attachment_list,
    }

    # For images, add the local file path from im_image_info
    # Also for quote/rich-text messages that contain [图片] markers
    if content_type == MSG_TYPE_IMAGE:
        msg["image_info"] = _get_image_info(conn, row["cid"], row["mid"], content_data)
    elif "[图片]" in text:
        msg["image_info"] = _get_image_info(conn, row["cid"], row["mid"], content_data)

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


def _local_path_to_url(local_path):
    """Convert a local DingTalk file path to a browser-accessible URL."""
    import os as _os
    # Extract the relative part after the DingTalk data dir
    data_dir = config.DINGTALK_DATA_DIR.rstrip(_os.sep) + _os.sep
    if local_path.startswith(data_dir):
        rel = local_path[len(data_dir):]
        return "/api/attachments/" + rel.replace("\\", "/")
    return ""


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


def _extract_quote_text(content_data):
    """Extract text from quote/re-edit messages (contentType=3100)."""
    ext = _extract_ext_from_attachment(content_data)
    if ext.get("desc"):
        return ext["desc"]
    if ext.get("title"):
        return ext["title"]
    # Fallback: try content_data.text
    return content_data.get("text", "")


def _extract_card_text(content_data):
    """Extract text from interactive card / mini-app card messages (contentType=2900/2950)."""
    ext = _extract_ext_from_attachment(content_data)
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
    print(f"Stats: {json.dumps(stats, ensure_ascii=False, indent=2)}")

    convs = get_conversations(conn, limit=5)
    print(f"\nTop 5 conversations:")
    for c in convs["conversations"]:
        print(f"  [{c['type']}] {c['title']} (members: {c['member_count']}, last: {c.get('last_modify', 0)})")

    if convs["conversations"]:
        cid = convs["conversations"][0]["cid"]
        msgs = get_messages(conn, cid, limit=3)
        print(f"\nLatest 3 messages from '{convs['conversations'][0]['title']}':")
        for m in msgs["messages"]:
            print(f"  [{m['created_at_str']}] {m['sender_name']}: {m['text'][:100]}")

    conn.close()
