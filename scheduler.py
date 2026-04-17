import json
import os
import logging
from datetime import datetime

import config
from decrypt import sync_decrypt
from exporter import export_incremental
from parser import get_connection, get_latest_message_time

logger = logging.getLogger(__name__)

# Global state
_sync_state = {
    "last_sync_time": None,       # ms timestamp
    "last_sync_time_str": None,   # human readable
    "last_export_path": None,
    "sync_count": 0,
    "is_syncing": False,
    "last_error": None,
    "next_sync_time": None,
}


def _load_state():
    """Load sync state from file."""
    global _sync_state
    if os.path.exists(config.SYNC_STATE_FILE):
        try:
            with open(config.SYNC_STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                _sync_state.update(saved)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load sync state: {e}")


def _save_state():
    """Save sync state to file."""
    try:
        with open(config.SYNC_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_sync_state, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logger.warning(f"Failed to save sync state: {e}")


def get_sync_state():
    """Get current sync state."""
    return _sync_state.copy()


def do_sync(full=False):
    """Execute a sync cycle: decrypt -> export incremental -> update state."""
    global _sync_state

    if _sync_state["is_syncing"]:
        logger.warning("Sync already in progress, skipping")
        return False

    _sync_state["is_syncing"] = True
    _sync_state["last_error"] = None
    _save_state()

    try:
        logger.info("=== Starting sync cycle ===")

        # Step 1: Decrypt
        decrypted_path = sync_decrypt()
        logger.info(f"Database decrypted: {decrypted_path}")

        # Step 2: Get latest message time
        conn = get_connection(decrypted_path)

        if full or not _sync_state["last_sync_time"]:
            # Full export on first run or when requested
            from exporter import export_all
            export_path = export_all()
            logger.info(f"Full export complete: {export_path}")
        else:
            # Incremental export
            export_path = export_incremental(_sync_state["last_sync_time"])
            if export_path:
                logger.info(f"Incremental export complete: {export_path}")
            else:
                logger.info("No new messages for incremental export")

        # Step 3: Update sync state
        latest_time = get_latest_message_time(conn)
        conn.close()

        _sync_state["last_sync_time"] = latest_time
        _sync_state["last_sync_time_str"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _sync_state["last_export_path"] = export_path
        _sync_state["sync_count"] += 1
        _sync_state["is_syncing"] = False

        _save_state()
        logger.info(f"=== Sync cycle complete (#{_sync_state['sync_count']}) ===")
        return True

    except Exception as e:
        logger.error(f"Sync failed: {e}", exc_info=True)
        _sync_state["is_syncing"] = False
        _sync_state["last_error"] = str(e)
        _save_state()
        return False


def setup_scheduler(app=None):
    """Set up APScheduler for periodic sync."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    _load_state()

    scheduler = BackgroundScheduler()

    # Add the sync job
    scheduler.add_job(
        func=do_sync,
        trigger=IntervalTrigger(hours=config.SYNC_INTERVAL_HOURS),
        id="dingtalk_sync",
        name="DingTalk DB Sync",
        replace_existing=True,
    )

    # Calculate next run time
    _sync_state["next_sync_time"] = "every {} hours".format(config.SYNC_INTERVAL_HOURS)

    logger.info(f"Scheduler configured: sync every {config.SYNC_INTERVAL_HOURS} hours")

    return scheduler


# Load state on module import
_load_state()
