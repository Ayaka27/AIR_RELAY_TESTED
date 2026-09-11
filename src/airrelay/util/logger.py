import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


class StructuredFormatter(logging.Formatter):
    """Human + machine friendly log lines used by the file handler."""

    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
        }
        extra = getattr(record, "fields", {})
        if extra:
            base.update(extra)
        base["msg"] = record.getMessage()
        if record.exc_info:
            base["exc"] = self.formatException(record.exc_info)
        return json.dumps(base, default=str)


class Logger:
    """Provides a structured logger for each subsystem."""

    def __init__(self) -> None:
        self._configured = False

    def configure(self, level: str = "INFO", log_dir: Optional[Path] = None) -> None:
        level_num = LEVELS.get(level.upper(), logging.INFO)
        root = logging.getLogger("airrelay")
        root.setLevel(level_num)
        # clear any previously attached handlers so configure() is idempotent
        for h in list(root.handlers):
            root.removeHandler(h)
        fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-24s %(message)s")
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)
        if log_dir is not None:
            log_dir = Path(log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                log_dir / "airrelay.jsonl", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            fh.setFormatter(StructuredFormatter())
            root.addHandler(fh)
        self._configured = True

    def get(self, name: str) -> "SysLogger":
        return SysLogger(logging.getLogger("airrelay." + name))


class SysLogger:
    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    def _emit(self, level: int, msg: str, **fields: Any) -> None:
        record_fields = dict(fields)
        self._log.log(level, msg, extra={"fields": record_fields})

    def debug(self, msg: str, **kw: Any) -> None:
        self._emit(logging.DEBUG, msg, **kw)

    def info(self, msg: str, **kw: Any) -> None:
        self._emit(logging.INFO, msg, **kw)

    def warning(self, msg: str, **kw: Any) -> None:
        self._emit(logging.WARNING, msg, **kw)

    def error(self, msg: str, **kw: Any) -> None:
        self._emit(logging.ERROR, msg, **kw)

    def critical(self, msg: str, **kw: Any) -> None:
        self._emit(logging.CRITICAL, msg, **kw)

    def exception(self, msg: str, **kw: Any) -> None:
        self._emit(logging.ERROR, msg, **kw)
        self._log.exception(msg)


_logger: Logger = Logger()


def get_logger(name: str) -> SysLogger:
    if not _logger._configured:
        _logger.configure()
    return _logger.get(name)
