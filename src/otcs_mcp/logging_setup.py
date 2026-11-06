# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Structured logging with correlation IDs and secret redaction (always to stderr)."""

import json
import logging
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import IO

from otcs_mcp.config import LogFormat, Settings
from otcs_mcp.observability import correlation_id_var

REDACTED = "***"
MIN_SECRET_LENGTH = 4  # shorter values would mangle ordinary words in every log line
FRAMEWORK_LOGGERS = ("fastmcp",)
_EXTRA_FIELDS = (
    "tool",
    "outcome",
    "seconds",
    "method",
    "path",
    "status",
    "client",
    "operation",
    "target_id",
)
_STANDARD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class RedactingFilter(logging.Filter):
    """Mask configured secrets in messages and exception texts before they are emitted."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        # Longest first, so a secret that contains another one is masked entirely.
        self._secrets = sorted(
            {s for s in secrets if len(s) >= MIN_SECRET_LENGTH}, key=len, reverse=True
        )

    def redact(self, text: str) -> str:
        """Return ``text`` with every secret replaced by ``***``."""
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = self.redact(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the correlation ID of the current request."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        correlation_id = correlation_id_var.get()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        for field in _EXTRA_FIELDS:
            if field in record.__dict__ and field not in _STANDARD_ATTRS:
                payload[field] = record.__dict__[field]
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        if record.exc_text:
            payload["exception"] = record.exc_text
        return json.dumps(payload, ensure_ascii=False, default=str)


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        correlation_id = correlation_id_var.get()
        suffix = f" [{correlation_id}]" if correlation_id else ""
        text = super().format(record)
        return f"{text}{suffix}"


def configure_logging(settings: Settings, *, stream: IO[str] | None = None) -> None:
    """Configure the root logger (idempotent). Never writes to stdout."""
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.addFilter(
        RedactingFilter(
            [
                settings.otcs_password.get_secret_value(),
                settings.mcp_http_auth_password.get_secret_value()
                if settings.mcp_http_auth_password
                else "",
            ]
        )
    )
    handler.setFormatter(
        JsonFormatter()
        if settings.log_format is LogFormat.JSON
        else _TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    for existing in [h for h in root.handlers if getattr(h, "_otcs_mcp", False)]:
        root.removeHandler(existing)
    handler._otcs_mcp = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(settings.log_level.value)
    _adopt_framework_loggers()


def _adopt_framework_loggers() -> None:
    """Route FastMCP's own logs through our handler.

    FastMCP installs its own (rich) handler at import time and stops propagation,
    which would bypass the JSON format and the secret redaction. Dropping those
    handlers and propagating to the root logger keeps one logging pipeline.
    """
    for name in FRAMEWORK_LOGGERS:
        framework_logger = logging.getLogger(name)
        for existing in framework_logger.handlers[:]:
            framework_logger.removeHandler(existing)
        framework_logger.propagate = True
        framework_logger.setLevel(logging.NOTSET)
