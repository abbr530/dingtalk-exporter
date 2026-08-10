import logging
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from log_utils import log_event
from web.api import app

import uvicorn

# Configure logging
log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(
    level=logging.INFO,
    # level=logging.DEBUG,
    format=log_format,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(config.LOGS_DIR, "dingtalk-exporter.log"),
            encoding="utf-8",
        ),
    ],
)

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    runtime_diag = config.get_runtime_diagnostics()
    log_event(
        logger,
        "info",
        "app.starting",
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        data_dir=config.DINGTALK_DATA_DIR,
        dingwave_path=config.DINGWAVE_PATH,
        sync_interval_hours=config.SYNC_INTERVAL_HOURS,
        config_source=runtime_diag["config_source"],
        candidates=runtime_diag["candidate_count"],
        selected_uid_masked=runtime_diag["user_uid_masked"],
        selected_uid_source_log=runtime_diag["selected_candidate"].get("uid_source_log"),
        is_v3=runtime_diag["is_v3"],
    )
    if runtime_diag["partial_env_override"]:
        log_event(
            logger,
            "warning",
            "app.partial_environment_override",
            env_uid_set=runtime_diag["env_override_uid"],
            env_dir_set=runtime_diag["env_override_data_dir"],
        )

    # Startup validation: warn early about missing components
    if not os.path.isfile(config.DINGWAVE_PATH):
        log_event(
            logger,
            "warning",
            "app.dingwave_missing",
            path=config.DINGWAVE_PATH,
            release_url="https://github.com/p1g3/dingwave/releases",
        )
    if not os.path.exists(config.ENCRYPTED_DB):
        log_event(
            logger,
            "warning",
            "app.encrypted_db_missing",
            path=config.ENCRYPTED_DB,
        )
    if config.DINGTALK_DATA_DIR.endswith("_v3"):
        log_event(
            logger,
            "warning",
            "app.v3_detected",
            data_dir=config.DINGTALK_DATA_DIR,
            hint="capture startup log and first manual sync if sync fails",
        )

    uvicorn.run(
        app,
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        log_level="info",
    )
