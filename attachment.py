import os
import shutil
import logging

import config

logger = logging.getLogger(__name__)


def get_local_image_path(image_info):
    """Resolve the actual local file path for an image."""
    if not image_info:
        return None

    local_path = image_info.get("local_path", "")
    if local_path and os.path.exists(local_path):
        return local_path

    return None


def get_attachment_export_path(message, attachment, export_dir, local_path=None):
    """Determine the export path for an attachment."""
    content_type = message.get("content_type", 0)
    att_type = attachment.get("type", 0)
    filename = attachment.get("filename", "")

    # Determine if this is an image based on content type or local file extension
    is_image = content_type == config.CONTENT_TYPE_IMAGE or att_type == config.CONTENT_TYPE_IMAGE
    if not is_image and local_path:
        ext = os.path.splitext(local_path)[1].lower()
        if ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"):
            is_image = True

    if is_image:
        subdir = "images"
    elif content_type == config.CONTENT_TYPE_VOICE or att_type == config.CONTENT_TYPE_VOICE:
        subdir = "audio"
    elif content_type == config.CONTENT_TYPE_FILE or att_type == config.CONTENT_TYPE_FILE:
        subdir = "files"
    else:
        subdir = "images" if is_image else "other"

    att_dir = os.path.join(export_dir, subdir)
    os.makedirs(att_dir, exist_ok=True)

    if not filename:
        # Use message id to generate a filename, preserve original extension
        if local_path:
            ext = os.path.splitext(local_path)[1] or _get_extension_for_type(content_type or att_type)
        else:
            ext = _get_extension_for_type(content_type or att_type)
        mid = message.get("id", "unknown")
        cid = message.get("cid", "unknown").split(":")[0]
        filename = f"{cid}_{mid}{ext}"

    # Sanitize filename
    filename = _sanitize_filename(filename)
    return os.path.join(att_dir, filename)


def copy_attachment_to_export(src_path, export_path):
    """Copy an attachment file to the export directory."""
    if not src_path or not os.path.exists(src_path):
        return False

    try:
        os.makedirs(os.path.dirname(export_path), exist_ok=True)
        shutil.copy2(src_path, export_path)
        return True
    except (OSError, shutil.Error) as e:
        logger.warning(f"Failed to copy attachment {src_path}: {e}")
        return False


def process_all_attachments(messages, export_dir):
    """Copy all attachment files (images, documents, etc.) to the export directory.

    Mutates message dicts in-place, adding:
      - msg["attachment_export_path"] for image messages
      - att["export_path"] for file attachments

    Returns stats dict with counts.
    """
    stats = {"images": 0, "files": 0, "skipped": 0, "errors": 0}

    for msg in messages:
        ct = msg.get("content_type", 0)
        img_info = msg.get("image_info", {})

        # Handle all messages with images (type 2, and quote/rich-text with [图片])
        if img_info and img_info.get("images"):
            exported_img_paths = []
            for idx, img in enumerate(img_info["images"]):
                local_path = img.get("local_path", "")
                if not local_path or not os.path.exists(local_path):
                    exported_img_paths.append(None)
                    stats["skipped"] += 1
                    continue
                export_path = get_attachment_export_path(msg, {}, export_dir, local_path=local_path)
                # For multi-image messages, append index to avoid collision
                if len(img_info["images"]) > 1:
                    name, ext = os.path.splitext(export_path)
                    export_path = f"{name}_{idx}{ext}"
                if copy_attachment_to_export(local_path, export_path):
                    rel_path = os.path.relpath(export_path, export_dir).replace("\\", "/")
                    exported_img_paths.append(rel_path)
                    stats["images"] += 1
                else:
                    exported_img_paths.append(None)
                    stats["errors"] += 1

            # Set primary path (backward compat)
            primary = next((p for p in exported_img_paths if p), None)
            if primary:
                msg["attachment_export_path"] = primary
            # Store all image paths for multi-image messages
            msg["image_export_paths"] = exported_img_paths
        elif ct == config.CONTENT_TYPE_IMAGE:
            # Fallback: single image without images list
            local_path = img_info.get("local_path", "") if img_info else ""
            if local_path and os.path.exists(local_path):
                export_path = get_attachment_export_path(msg, {}, export_dir)
                if copy_attachment_to_export(local_path, export_path):
                    msg["attachment_export_path"] = os.path.relpath(
                        export_path, export_dir
                    ).replace("\\", "/")
                    stats["images"] += 1
                else:
                    stats["errors"] += 1
            else:
                stats["skipped"] += 1

        # Handle file attachments in attachments array
        for att in msg.get("attachments", []):
            if not att.get("local_available") or not att.get("local_path"):
                if att.get("filename"):
                    stats["skipped"] += 1
                continue

            src = att["local_path"]
            export_path = get_attachment_export_path(msg, att, export_dir)

            # Handle filename collision by appending message id
            if os.path.exists(export_path):
                name, ext = os.path.splitext(export_path)
                mid = msg.get("id", "unknown")
                export_path = f"{name}_{mid}{ext}"

            if copy_attachment_to_export(src, export_path):
                att["export_path"] = os.path.relpath(
                    export_path, export_dir
                ).replace("\\", "/")
                stats["files"] += 1
            else:
                stats["errors"] += 1

    logger.info(
        f"Attachment export stats: {stats['images']} images, "
        f"{stats['files']} files, {stats['skipped']} skipped, "
        f"{stats['errors']} errors"
    )
    return stats


def _get_extension_for_type(content_type):
    """Get file extension for a content type."""
    type_map = {
        config.CONTENT_TYPE_IMAGE: ".jpg",
        config.CONTENT_TYPE_VOICE: ".amr",
        config.CONTENT_TYPE_FILE: ".bin",
    }
    return type_map.get(content_type, ".bin")


def _sanitize_filename(filename):
    """Remove or replace characters that are invalid in filenames."""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, "_")
    # Limit length
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[:200] + ext
    return filename
