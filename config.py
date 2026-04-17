import os
import logging

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

logger = logging.getLogger(__name__)


def _detect_dingtalk_user():
    """Auto-detect DingTalk user data directory and UID.

    Scans %APPDATA%\\DingTalk\\ for *_v2 directories.
    Returns (data_dir, uid) or (None, None) if not found.

    Override with environment variables:
      DINGTALK_UID       — your DingTalk user UID
      DINGTALK_DATA_DIR  — full path to the *_v2 directory
    """
    # Environment variables take highest priority
    env_uid = os.environ.get("DINGTALK_UID", "").strip()
    env_dir = os.environ.get("DINGTALK_DATA_DIR", "").strip()

    if env_uid and env_dir:
        logger.info(f"Using config from environment: UID={env_uid}")
        return env_dir, env_uid

    # Scan for *_v2 directories under %APPDATA%\DingTalk\
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return None, None

    dingtalk_base = os.path.join(appdata, "DingTalk")
    if not os.path.isdir(dingtalk_base):
        return None, None

    # Find all *_v2 user directories that have a database file
    v2_dirs = []
    for entry in os.listdir(dingtalk_base):
        if entry.endswith("_v2"):
            full_path = os.path.join(dingtalk_base, entry)
            if os.path.isdir(full_path):
                db_file = os.path.join(full_path, "DBFiles", "dingtalk.db")
                if os.path.exists(db_file):
                    uid = entry.replace("_v2", "")
                    mtime = os.path.getmtime(db_file)
                    v2_dirs.append((uid, full_path, mtime))

    if not v2_dirs:
        return None, None

    if len(v2_dirs) == 1:
        uid, path, _ = v2_dirs[0]
        logger.info(f"Auto-detected DingTalk user: UID={uid}")
        return path, uid

    # Multiple users: pick the one with most recently modified database
    v2_dirs.sort(key=lambda x: x[2], reverse=True)
    uid, path, _ = v2_dirs[0]
    all_uids = [f"  UID={u} (path={p})" for u, p, _ in v2_dirs]
    logger.warning(
        f"Multiple DingTalk users found, using the most recent (UID={uid}):\n"
        + "\n".join(all_uids)
        + "\nTo use a different user, set DINGTALK_UID and DINGTALK_DATA_DIR environment variables."
    )
    return path, uid


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

ENCRYPTED_DB_DIR = os.path.join(DINGTALK_DATA_DIR, "DBFiles")
ENCRYPTED_DB = os.path.join(ENCRYPTED_DB_DIR, "dingtalk.db")

# dingwave tool
DINGWAVE_PATH = os.path.join(PROJECT_DIR, "tools", "dingwave.exe")

# Sync settings
SYNC_INTERVAL_HOURS = 4
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
WEB_HOST = "0.0.0.0"
WEB_PORT = 8090

# Ensure directories exist
for d in [DATA_DIR, DECRYPTED_DIR, EXPORT_DIR, LOGS_DIR]:
    os.makedirs(d, exist_ok=True)
