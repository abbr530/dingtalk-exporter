import json


def _event_text(event, fields):
    templates = {
        "app.starting": "应用启动，正在初始化 Web 服务",
        "app.partial_environment_override": "环境变量覆盖配置不完整，将继续使用自动检测结果",
        "app.dingwave_missing": "未找到 dingwave 可执行文件，语音导出功能可能不可用",
        "app.encrypted_db_missing": "未找到钉钉加密数据库，请确认客户端已登录且数据目录正确",
        "app.v3_detected": "检测到钉钉 V3 数据目录，如同步异常请优先查看启动日志和首次手动同步日志",
        "attachment.copy_failed": "复制附件失败",
        "attachment.processed": "附件处理完成",
        "config.redirect_read_failed": "读取钉钉数据重定向配置失败",
        "config.redirect_detected": "检测到钉钉数据目录重定向配置",
        "config.v3_log_scan_failed": "扫描钉钉 V3 日志失败，无法从日志辅助识别 UID",
        "config.v3_uid_resolved": "已通过钉钉 V3 日志识别到实际 UID",
        "config.source_environment": "已通过环境变量指定钉钉数据目录",
        "config.partial_environment_override": "环境变量仅设置了一部分，未生效的部分将继续自动检测",
        "config.auto_detected": "已自动识别到钉钉数据目录",
        "config.multiple_users_detected": "检测到多个钉钉账号目录，已优先选择最近更新的数据库",
        "config.dingwave_detected": "已自动识别到 dingwave 可执行文件",
        "decrypt.copy_succeeded": "已复制加密数据库到临时目录",
        "decrypt.copy_retry_failed": "复制加密数据库失败，准备重试",
        "decrypt.v3_user_config_loaded": "已读取钉钉 V3 user_config 配置",
        "decrypt.mode_selected": f"已选择 {str(fields.get('mode', '')).upper()} 解密模式".strip(),
        "decrypt.uid_candidate_failed": "当前 UID 解密失败，准备尝试下一个候选 UID",
        "decrypt.header_mismatch": "解密结果校验失败，输出文件不是有效的 SQLite 数据库",
        "decrypt.started": "开始解密钉钉数据库",
        "decrypt.completed": "数据库解密完成",
        "decrypt.wal_completed": "WAL 增量日志解密完成",
        "decrypt.snapshot_inspect_failed": "读取解密快照中的最新消息时间失败",
        "decrypt.snapshot_export_read_failed": "读取上次导出结果失败，无法恢复已知最新时间",
        "decrypt.snapshot_state_read_failed": "读取同步状态失败，无法恢复已知最新时间",
        "decrypt.snapshot_promoted": "候选解密快照已通过校验并替换当前快照",
        "decrypt.snapshot_fallback_used": "本次候选快照未通过校验，已继续沿用当前解密快照",
        "decrypt.temp_dir_cleaned": "临时解密目录已清理",
        "decrypt.sync_started": "开始执行数据库解密同步",
        "decrypt.sync_completed": "数据库解密同步完成",
        "export.full_started": "开始执行全量导出",
        "export.full_conversations_loaded": "已加载会话列表，准备导出消息",
        "export.full_progress": "全量导出进行中",
        "export.attachments_processing": "开始处理附件导出",
        "export.attachments_processed": "附件导出处理完成",
        "export.full_completed": "全量导出完成",
        "export.incremental_started": "开始执行增量导出",
        "export.incremental_messages_loaded": "已加载增量消息",
        "export.incremental_skipped": "本次增量导出跳过，没有发现新消息",
        "export.incremental_completed": "增量导出完成",
        "export.selected_started": "开始导出指定会话",
        "export.selected_completed": "指定会话导出完成",
        "parser.db_inspect_failed": "读取解密数据库文件信息失败",
        "parser.db_not_usable": "解密数据库当前不可用",
        "scheduler.state_load_failed": "读取同步状态文件失败",
        "scheduler.state_save_failed": "保存同步状态文件失败",
        "scheduler.sync_skipped": "同步任务跳过，当前已有任务正在执行",
        "scheduler.sync_started": "开始执行同步任务",
        "scheduler.decrypt_ready": "解密数据库已就绪",
        "scheduler.export_completed": "导出任务完成",
        "scheduler.export_skipped": "导出任务跳过，没有可导出的新消息",
        "scheduler.cursor_updated": "同步游标已更新",
        "scheduler.sync_completed": "同步任务完成",
        "scheduler.sync_failed": "同步任务失败",
        "scheduler.configured": "定时同步任务已配置完成",
    }
    return templates.get(event, event)


def _format_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    if any(ch.isspace() for ch in text) or "|" in text or "=" in text:
        return json.dumps(text, ensure_ascii=False)
    return text


def format_fields(**fields):
    parts = []
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={_format_value(value)}")
    return " ".join(parts)


def log_event(logger, level, event, **fields):
    message = _event_text(event, fields)
    details = format_fields(event=event, **fields)
    if details:
        message = f"{message} | {details}"
    getattr(logger, level)(message)
