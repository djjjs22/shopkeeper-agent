"""
日志初始化

集中管理项目的日志输出行为，包括统一日志格式、注入 request_id 以及按配置输出到控制台和文件。
业务代码只需要导入这里的 logger，就可以使用同一套日志能力。

格式切换（2026-07-17 改造）：
- 默认：人类可读的彩色文本（本地开发友好）
- LOG_FORMAT=json：每行一个 JSON 对象，便于 ELK / Loki / Grafana 等日志聚合工具消费
  切换方式：在启动前 export LOG_FORMAT=json（或者在 .env 里加 LOG_FORMAT=json）
"""

import json
import os
import sys
from pathlib import Path

from loguru import logger

from app.conf.app_config import app_config
from app.core.context import request_id_ctx_var

# 全局格式开关：env 变量驱动，运行时只读一次
_LOG_FORMAT = os.getenv("LOG_FORMAT", "human").strip().lower()


# 人类可读格式（保留原样，本地开发/容器 stdout 默认）
_HUMAN_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<magenta>request_id - {extra[request_id]}</magenta> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>\n"
)


def inject_request_id(record):
    """把上下文中的 request_id 注入到每条日志的 extra 字段"""
    request_id = request_id_ctx_var.get()
    record["extra"]["request_id"] = request_id


# 移除 Loguru 默认的输出目标，避免和项目自定义配置重复打印
logger.remove()

# 生成带 request_id 注入能力的 logger，后续业务代码统一使用这个实例
logger = logger.patch(inject_request_id)

# 2026-07-17 改造（bug fix）：之前手写的 _json_formatter 被 loguru 当成
# str.format 模板使用，找不到名为 "ts" 的 key 报 KeyError，所有 JSON 日志被吞掉。
# 改用 loguru 内置 serialize=True —— 一行参数搞定，输出标准 JSON，
# 自动包含 timestamp/level/message/extra 所有字段。
_USE_JSON = _LOG_FORMAT == "json"

# 根据配置决定是否输出控制台日志，适合本地开发和容器标准输出采集
if app_config.logging.console.enable:
    logger.add(
        sink=sys.stdout,
        level=app_config.logging.console.level,
        format=_HUMAN_FORMAT if not _USE_JSON else None,
        serialize=_USE_JSON,
    )

# 根据配置决定是否写入文件日志，并在启动时确保日志目录存在
if app_config.logging.file.enable:
    path = Path(app_config.logging.file.path)
    path.mkdir(parents=True, exist_ok=True)
    logger.add(
        sink=path / "app.log",
        level=app_config.logging.file.level,
        format=_HUMAN_FORMAT if not _USE_JSON else None,
        serialize=_USE_JSON,
        rotation=app_config.logging.file.rotation,
        retention=app_config.logging.file.retention,
        encoding="utf-8",
    )