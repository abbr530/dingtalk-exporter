import json
import os
import logging
from datetime import datetime

import config
from decrypt import sync_decrypt
from exporter import export_incremental
from log_utils import log_event
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


def _apply_sync_overlap(timestamp_ms):
    """Move the incremental cursor back slightly to avoid message gaps."""
    if not timestamp_ms:
        return 0
    overlap_ms = max(0, int(config.SYNC_OVERLAP_SECONDS) * 1000)
    return max(0, int(timestamp_ms) - overlap_ms)


def _load_state():
    """Load sync state from file."""
    global _sync_state
    if os.path.exists(config.SYNC_STATE_FILE):
        try:
            with open(config.SYNC_STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                _sync_state.update(saved)
        except (json.JSONDecodeError, IOError) as e:
            log_event(logger, "warning", "scheduler.state_load_failed", path=config.SYNC_STATE_FILE, error=e)

    # A freshly started process can never be mid-sync; clear any stale
    # is_syncing flag left behind by a crashed or killed process.
    if _sync_state.get("is_syncing"):
        _sync_state["is_syncing"] = False
        log_event(logger, "info", "scheduler.stale_sync_flag_cleared")


def _save_state():
    """Save sync state to file."""
    try:
        with open(config.SYNC_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_sync_state, f, ensure_ascii=False, indent=2)
    except IOError as e:
        log_event(logger, "warning", "scheduler.state_save_failed", path=config.SYNC_STATE_FILE, error=e)


def get_sync_state():
    """Get current sync state."""
    return _sync_state.copy()


def do_sync(full=False):
    """Execute a sync cycle: decrypt -> export incremental -> update state."""
    global _sync_state

    if _sync_state["is_syncing"]:
        log_event(logger, "warning", "scheduler.sync_skipped", reason="already_in_progress")
        return False

    _sync_state["is_syncing"] = True
    _sync_state["last_error"] = None
    _save_state()

    try:
        log_event(logger, "info", "scheduler.sync_started", full=full)
        previous_sync_time = _sync_state["last_sync_time"] or 0

        # Step 1: Decrypt
        decrypted_path = sync_decrypt()
        log_event(logger, "info", "scheduler.decrypt_ready", path=decrypted_path)

        if full or not _sync_state["last_sync_time"]:
            # Full export on first run or when requested
            from exporter import export_all
            export_path = export_all()
            exported_latest_time = None
            log_event(logger, "info", "scheduler.export_completed", mode="full", path=export_path)
        else:
            # Incremental export
            export_result = export_incremental(_sync_state["last_sync_time"])
            export_path = export_result["path"]
            exported_latest_time = export_result["max_created_at"]
            if export_path:
                log_event(
                    logger,
                    "info",
                    "scheduler.export_completed",
                    mode="incremental",
                    path=export_path,
                    message_count=export_result["message_count"],
                    max_created_at=exported_latest_time,
                )
            else:
                log_event(logger, "info", "scheduler.export_skipped", reason="no_new_messages")

        # Step 3: Re-open the database after export so we observe the final
        # post-WAL state instead of an earlier reader snapshot.
        conn = get_connection(decrypted_path)
        latest_time = get_latest_message_time(conn)
        conn.close()

        if full or not previous_sync_time:
            next_sync_time = _apply_sync_overlap(latest_time)
        elif exported_latest_time:
            next_sync_time = _apply_sync_overlap(exported_latest_time)
        else:
            next_sync_time = previous_sync_time

        log_event(
            logger,
            "info",
            "scheduler.cursor_updated",
            previous_sync_time=previous_sync_time,
            db_latest_time=latest_time,
            exported_latest_time=exported_latest_time,
            next_sync_time=next_sync_time,
            overlap_seconds=config.SYNC_OVERLAP_SECONDS,
        )

        _sync_state["last_sync_time"] = next_sync_time
        _sync_state["last_sync_time_str"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if export_path:
            _sync_state["last_export_path"] = export_path
        _sync_state["sync_count"] += 1
        _sync_state["is_syncing"] = False

        _save_state()
        log_event(
            logger,
            "info",
            "scheduler.sync_completed",
            sync_count=_sync_state["sync_count"],
            last_sync_time=_sync_state["last_sync_time"],
            export_path=export_path,
        )
        return True

    except Exception as e:
        log_event(logger, "error", "scheduler.sync_failed", error=e)
        logger.exception("同步任务失败，异常堆栈如下")
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

    log_event(
        logger,
        "info",
        "scheduler.configured",
        interval_hours=config.SYNC_INTERVAL_HOURS,
        next_sync_time=_sync_state["next_sync_time"],
    )

    return scheduler


# Load state on module import
_load_state()
