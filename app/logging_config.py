"""
Structured JSON logging.

Configures the root logger with JSON output on stdout, propagating
request_id and organization_id via ContextVar for async-safe correlation.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

from pythonjsonlogger.json import JsonFormatter

# Async-safe context variables for request correlation
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
org_id_var: ContextVar[str] = ContextVar("org_id", default="")


class LogintelJsonFormatter(JsonFormatter):
    """JSON log formatter that injects request_id and organization_id."""

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = self.formatTime(record)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record["request_id"] = request_id_var.get()
        log_record["organization_id"] = org_id_var.get()


def setup_logging(log_level: str = "INFO") -> None:
    """Configure the root logger with a JSON formatter on stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(LogintelJsonFormatter(fmt="%(timestamp)s %(level)s %(logger)s %(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Silence noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
