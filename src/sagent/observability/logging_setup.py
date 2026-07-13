"""日志系统搭建。

提供统一的日志初始化、JSON 结构化格式化、按天滚动的文件日志，以及基于
contextvars 的 trace_id 全链路关联能力。

设计要点：
- 文件日志：JSON 每行一条，按天滚动（TimedRotatingFileHandler），便于按日期定位。
- 控制台日志：简洁纯文本，默认 INFO，避免刷屏；不影响 CLI 面向用户的 print 展示。
- trace_id：每次用户问答生成一个短 id 注入到当次全部日志，便于串联整条链路检索。
- 事件专有字段通过 logger.xxx(msg, extra={...}) 传入，会被序列化进 JSON。
"""

from __future__ import annotations

import json
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from ..config.models import LoggingConfig

# 当前请求的 trace_id（跨函数调用共享）
_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")

# 是否记录 LLM 完整内容（由 setup_logging 依据配置设置）
_log_llm_content = False

# logging.LogRecord 的内置属性名集合，用于从 record 中筛出自定义 extra 字段
_RESERVED_ATTRS = set(
    logging.makeLogRecord({}).__dict__.keys()
) | {"message", "asctime", "trace_id"}


def new_trace_id() -> str:
    """生成一个短 trace_id 并设置为当前上下文的 trace_id。"""
    trace_id = uuid.uuid4().hex[:8]
    set_trace_id(trace_id)
    return trace_id


def set_trace_id(trace_id: str) -> None:
    """设置当前上下文的 trace_id。"""
    _trace_id_var.set(trace_id)


def current_trace_id() -> str:
    """获取当前上下文的 trace_id。"""
    return _trace_id_var.get()


def log_llm_content_enabled() -> bool:
    """是否允许记录 LLM 完整请求/响应内容。"""
    return _log_llm_content


def get_logger(name: str) -> logging.Logger:
    """获取指定名称的 logger。"""
    return logging.getLogger(name)


class _TraceIdFilter(logging.Filter):
    """将当前上下文的 trace_id 注入到每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = current_trace_id()
        return True


class _JsonFormatter(logging.Formatter):
    """将日志记录格式化为单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "trace_id": getattr(record, "trace_id", "-"),
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # 附加通过 extra 传入的自定义字段
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                data[key] = _safe(value)

        # 异常堆栈
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)

        return json.dumps(data, ensure_ascii=False)


def _safe(value: Any) -> Any:
    """尽力将值转换为可 JSON 序列化的形式。"""
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return str(value)


def setup_logging(config: LoggingConfig) -> None:
    """根据配置初始化日志系统（幂等：重复调用会先清理已有 handler）。"""
    global _log_llm_content
    _log_llm_content = config.log_llm_content

    root = logging.getLogger("sagent")
    root.setLevel(logging.DEBUG)
    root.propagate = False

    # 清理旧 handler，避免重复添加
    for handler in list(root.handlers):
        root.removeHandler(handler)

    if not config.enabled:
        root.addHandler(logging.NullHandler())
        return

    trace_filter = _TraceIdFilter()

    # 控制台：简洁纯文本
    console = logging.StreamHandler()
    console.setLevel(_level(config.console_level))
    console.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(trace_id)s] %(name)s - %(message)s")
    )
    console.addFilter(trace_filter)
    root.addHandler(console)

    # 文件：JSON，按天滚动
    log_dir = Path(config.dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        filename=log_dir / config.file,
        when="midnight",
        backupCount=config.backup_count,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setLevel(_level(config.level))
    file_handler.setFormatter(_JsonFormatter())
    file_handler.addFilter(trace_filter)
    root.addHandler(file_handler)


def _level(name: str) -> int:
    """将级别名称转换为 logging 级别数值，非法时回退 INFO。"""
    return logging.getLevelName(name.upper()) if isinstance(
        logging.getLevelName(name.upper()), int
    ) else logging.INFO
