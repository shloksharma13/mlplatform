"""Structured JSON logging with request-scoped correlation identifiers."""

from __future__ import annotations

import contextvars
import datetime as dt
import json
import logging
from typing import Any

request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)

_STANDARD_LOG_RECORD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Render one compact JSON object per log record."""

    def __init__(self, service: str) -> None:
        """Create a formatter that adds a stable service name to every record."""
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        """Serialize the record with the platform's mandatory fields."""
        payload: dict[str, Any] = {
            "ts": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "request_id": request_id_context.get(),
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_FIELDS and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(service: str, level: str = "INFO") -> logging.Logger:
    """Configure the root logger for JSON stdout logging and return it."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    return root


def bind_request_id(request_id: str) -> contextvars.Token[str]:
    """Bind a request ID in the current async context and return its reset token."""
    return request_id_context.set(request_id)


def reset_request_id(token: contextvars.Token[str]) -> None:
    """Restore the request ID context represented by ``token``."""
    request_id_context.reset(token)
