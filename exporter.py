import json
import os
import shutil
import logging
from datetime import datetime

import config
from log_utils import log_event
from parser import get_connection, get_conversations, get_messages, get_new_messages, _format_timestamp
from attachment import process_all_attachments

logger = logging.getLogger(__name__)


def _create_export_dir(base_dir, prefix):
    """Create a timestamped export directory and return its path."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_dir = os.path.join(base_dir, f"{prefix}_{timestamp}")
    os.makedirs(export_dir, exist_ok=True)
    return export_dir


def _write_export_json(export_data, export_dir):
    """Write the export JSON file and return its path."""
    json_path = os.path.join(export_dir, "export.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)
    return json_path


def export_all(base_dir=None, batch_size=500):
    """Export all messages as a self-contained directory with attachments."""
    if base_dir is None:
        base_dir = config.EXPORT_DIR

    os.makedirs(base_dir, exist_ok=True)
    conn = get_connection()

    export_dir = _create_export_dir(base_dir, "full_export")
    log_event(logger, "info", "export.full_started", export_dir=export_dir, batch_size=batch_size)

    convs_result = get_conversations(conn, limit=10000)
    total_convs = convs_result["total"]
    log_event(logger, "info", "export.full_conversations_loaded", total_conversations=total_convs)

    export_data = {
        "export_time": datetime.now().isoformat(),
        "export_type": "full",
        "total_conversations": total_convs,
        "conversations": [],
    }

    # Collect raw message dicts for attachment processing
    all_raw_messages = []

    for idx, conv in enumerate(convs_result["conversations"]):
        cid = conv["cid"]
        conv_data = {
            "conversation_id": cid,
            "title": conv["title"],
            "type": conv["type"],
            "member_count": conv.get("member_count", 0),
            "messages": [],
        }

        # Fetch all messages for this conversation
        offset = 0
        while True:
            result = get_messages(conn, cid, limit=batch_size, offset=offset)
            all_raw_messages.extend(result["messages"])
            if offset + batch_size >= result["total"]:
                break
            offset += batch_size

        conv_data["message_count"] = len(
            [m for m in all_raw_messages if m["cid"] == cid]
        )
        export_data["conversations"].append(conv_data)

        if (idx + 1) % 50 == 0:
            log_event(
                logger,
                "info",
                "export.full_progress",
                fetched_conversations=idx + 1,
                total_conversations=total_convs,
            )

    # Copy attachments before serialization
    log_event(
        logger,
        "info",
        "export.attachments_processing",
        mode="full",
        message_count=len(all_raw_messages),
        export_dir=export_dir,
    )
    stats = process_all_attachments(all_raw_messages, export_dir)
    log_event(logger, "info", "export.attachments_processed", mode="full", stats=stats)

    # Serialize messages into conversation groups
    msg_by_cid = {}
    for msg in all_raw_messages:
        cid = msg["cid"]
        if cid not in msg_by_cid:
            msg_by_cid[cid] = []
        msg_by_cid[cid].append(msg)

    for conv_data in export_data["conversations"]:
        cid = conv_data["conversation_id"]
        conv_data["messages"] = [
            _serialize_message(m) for m in msg_by_cid.get(cid, [])
        ]

    json_path = _write_export_json(export_data, export_dir)
    conn.close()
    log_event(
        logger,
        "info",
        "export.full_completed",
        export_dir=export_dir,
        json_path=json_path,
        total_conversations=total_convs,
        total_messages=len(all_raw_messages),
    )
    return export_dir


def export_incremental(since_time, base_dir=None):
    """Export only new messages since the given timestamp, with attachments."""
    if base_dir is None:
        base_dir = config.EXPORT_DIR

    os.makedirs(base_dir, exist_ok=True)
    conn = get_connection()

    export_dir = _create_export_dir(base_dir, "incremental")

    since_str = datetime.fromtimestamp(since_time / 1000).isoformat() if since_time else "beginning"
    log_event(
        logger,
        "info",
        "export.incremental_started",
        export_dir=export_dir,
        since_time=since_time,
        since_time_str=since_str,
    )

    # Get all new messages
    all_messages = get_new_messages(conn, since_time)
    log_event(logger, "info", "export.incremental_messages_loaded", total_messages=len(all_messages))

    if not all_messages:
        log_event(logger, "info", "export.incremental_skipped", reason="no_new_messages")
        conn.close()
        # Remove empty export directory
        try:
            os.rmdir(export_dir)
        except OSError:
            pass
        return {
            "path": None,
            "message_count": 0,
            "min_created_at": None,
            "max_created_at": None,
        }

    # Copy attachments before serialization
    log_event(
        logger,
        "info",
        "export.attachments_processing",
        mode="incremental",
        message_count=len(all_messages),
        export_dir=export_dir,
    )
    stats = process_all_attachments(all_messages, export_dir)
    log_event(logger, "info", "export.attachments_processed", mode="incremental", stats=stats)

    # Group by conversation
    conv_messages = {}
    for msg in all_messages:
        cid = msg["cid"]
        if cid not in conv_messages:
            conv_messages[cid] = {
                "conversation_id": cid,
                "title": "",
                "type": "",
                "messages": [],
            }
        conv_messages[cid]["messages"].append(_serialize_message(msg))

    # Fill in conversation details
    for cid, conv_data in conv_messages.items():
        conn_row = conn.execute(
            "SELECT type, title FROM tbconversation WHERE cid = ?",
            (cid,),
        ).fetchone()
        if conn_row:
            conv_data["title"] = conn_row["title"] or ""
            conv_data["type"] = "single" if conn_row["type"] == 1 else "group"

    export_data = {
        "export_time": datetime.now().isoformat(),
        "export_type": "incremental",
        "from_time": since_str,
        "total_messages": len(all_messages),
        "conversations": list(conv_messages.values()),
    }

    json_path = _write_export_json(export_data, export_dir)
    conn.close()
    log_event(
        logger,
        "info",
        "export.incremental_completed",
        export_dir=export_dir,
        json_path=json_path,
        total_messages=len(all_messages),
        total_conversations=len(conv_messages),
    )
    return {
        "path": export_dir,
        "message_count": len(all_messages),
        "min_created_at": all_messages[0]["created_at"],
        "max_created_at": all_messages[-1]["created_at"],
    }


def _serialize_message(msg):
    """Serialize a message dict for JSON export."""
    result = {
        "message_id": msg.get("id"),
        "sender_id": msg.get("sender_id"),
        "sender_name": msg.get("sender_name"),
        "content_type": msg.get("content_type"),
        "content_type_name": msg.get("content_type_name"),
        "message_subtype": msg.get("message_subtype"),
        "text": msg.get("text", ""),
        "created_at": msg.get("created_at"),
        "created_at_str": msg.get("created_at_str"),
        "attachments": msg.get("attachments", []),
        "at_ids": msg.get("at_ids", {}),
    }
    # Include image info for image messages
    if msg.get("image_info"):
        result["image_info"] = msg["image_info"]
    if msg.get("audio_info"):
        result["audio_info"] = msg["audio_info"]
    if msg.get("quote_info"):
        result["quote_info"] = msg["quote_info"]
    if msg.get("is_ding"):
        result["is_ding"] = msg["is_ding"]
    # Include attachment export path if processed
    if msg.get("attachment_export_path"):
        result["attachment_export_path"] = msg["attachment_export_path"]
    # Include all image export paths for multi-image messages
    if msg.get("image_export_paths"):
        result["image_export_paths"] = msg["image_export_paths"]
    # Generate agent-friendly content with inline attachment references
    result["content"] = _build_agent_content(msg)
    return result


def _build_agent_content(msg):
    """Build a unified content string with inline attachment references for AI agents.

    Replaces [图片]/[文件]/[语音] markers with actual file paths, and appends
    downloadable file references at the end, so the agent sees one cohesive string.
    """
    text = msg.get("text", "")
    ct = msg.get("content_type", 0)
    at_ids = msg.get("at_ids", {})

    # Resolve @mentions in text: replace @uid with @name
    if at_ids and isinstance(at_ids, dict):
        for uid, name in at_ids.items():
            text = text.replace(f"@{uid}", f"@{name}")

    content = text

    # --- Replace [图片] markers with actual image paths ---
    # Use image_export_paths (list) for multi-image messages, fallback to single path
    img_paths = msg.get("image_export_paths", [])
    if not img_paths:
        single = msg.get("attachment_export_path", "")
        if single:
            img_paths = [single]

    if ct == 2:  # Image message
        if img_paths and img_paths[0]:
            content = f"[图片: {img_paths[0]}]"
        else:
            content = "[图片未下载到本地]"
    elif "[图片]" in content:
        # Quote/rich text with inline image markers — replace each [图片] in order
        for img_path in img_paths:
            if img_path:
                content = content.replace("[图片]", f"[图片: {img_path}]", 1)
            else:
                content = content.replace("[图片]", "[图片未下载到本地]", 1)
        # Any remaining [图片] markers without matching paths
        content = content.replace("[图片]", "[图片未下载到本地]")

    # --- Replace [语音] marker ---
    content = content.replace("[语音]", "[语音消息]")

    # --- Append file attachment references ---
    att_lines = []
    for att in msg.get("attachments", []):
        fname = att.get("filename", "")
        if not fname:
            continue
        export_path = att.get("export_path", "")
        fsize = att.get("file_size", 0)

        if export_path:
            size_str = _format_file_size(fsize) if fsize else ""
            ref = f"[附件: {fname}"
            if size_str:
                ref += f" ({size_str})"
            ref += f" | {export_path}]"
            att_lines.append(ref)
        elif att.get("local_available") is False:
            att_lines.append(f"[附件未下载: {fname}]")

        # Replace [文件] in text with the first attachment reference
        if "[文件]" in content and export_path:
            content = content.replace("[文件]", "", 1)

    if att_lines:
        if content and content.strip():
            content += "\n"
        content += "\n".join(att_lines)

    return content.strip()


def _format_file_size(size):
    """Format bytes to human-readable size."""
    if not size:
        return ""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.0f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def export_by_cids(cids, base_dir=None, batch_size=500, since_time=None, until_time=None):
    """Export only the selected conversations by cid list, with attachments.

    Args:
        since_time: Optional millisecond timestamp. Only export messages after this time.
    """
    if base_dir is None:
        base_dir = config.EXPORT_DIR

    os.makedirs(base_dir, exist_ok=True)
    conn = get_connection()

    export_dir = _create_export_dir(base_dir, "export")
    time_desc = f" (since {_format_timestamp(since_time)})" if since_time else ""
    log_event(
        logger,
        "info",
        "export.selected_started",
        export_dir=export_dir,
        conversation_count=len(cids),
        since_time=since_time,
        until_time=until_time,
        time_desc=time_desc.strip() or None,
        batch_size=batch_size,
    )

    export_data = {
        "export_time": datetime.now().isoformat(),
        "export_type": "selected",
        "since_time": since_time,
        "total_conversations": len(cids),
        "conversations": [],
    }

    # Collect raw messages for attachment processing
    all_raw_messages = []

    for cid in cids:
        conv_row = conn.execute(
            "SELECT type, title, memberCount FROM tbconversation WHERE cid = ?",
            (cid,),
        ).fetchone()
        conv_data = {
            "conversation_id": cid,
            "title": conv_row["title"] if conv_row else "",
            "type": "single" if (conv_row and conv_row["type"] == 1) else "group",
            "member_count": conv_row["memberCount"] if conv_row else 0,
            "messages": [],
        }

        offset = 0
        while True:
            result = get_messages(conn, cid, limit=batch_size, offset=offset, since_time=since_time, until_time=until_time)
            all_raw_messages.extend(result["messages"])
            if offset + batch_size >= result["total"]:
                break
            offset += batch_size

        export_data["conversations"].append(conv_data)

    # Copy attachments before serialization
    log_event(
        logger,
        "info",
        "export.attachments_processing",
        mode="selected",
        message_count=len(all_raw_messages),
        export_dir=export_dir,
    )
    stats = process_all_attachments(all_raw_messages, export_dir)
    log_event(logger, "info", "export.attachments_processed", mode="selected", stats=stats)

    # Serialize messages into conversation groups
    msg_by_cid = {}
    for msg in all_raw_messages:
        cid = msg["cid"]
        if cid not in msg_by_cid:
            msg_by_cid[cid] = []
        msg_by_cid[cid].append(msg)

    for conv_data in export_data["conversations"]:
        cid = conv_data["conversation_id"]
        conv_data["messages"] = [
            _serialize_message(m) for m in msg_by_cid.get(cid, [])
        ]
        conv_data["message_count"] = len(conv_data["messages"])

    json_path = _write_export_json(export_data, export_dir)
    conn.close()
    log_event(
        logger,
        "info",
        "export.selected_completed",
        export_dir=export_dir,
        json_path=json_path,
        total_conversations=len(cids),
        total_messages=len(all_raw_messages),
    )
    return export_dir


if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    logging.basicConfig(level=logging.INFO)
    path = export_all()
    print(f"导出结果已保存到：{path}")
