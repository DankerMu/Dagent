"""Unified logging configuration for xagent web application."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from logging.config import dictConfig
from typing import Literal, cast

from ..core.utils.security import redact_sensitive_text

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_VALID_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_REDACTED = "[REDACTED]"

# Named credential keys in quoted JSON/Python mappings. Kept as an explicit
# closed set rather than a general PII detector: unlabeled secrets, encoded
# blobs, and keys outside this list are not claimed.
_QUOTED_CREDENTIAL_KEYS = (
    "authorization",
    "password",
    "passwd",
    "secret",
    "token",
    "api[_-]?key",
    "access[_-]?token",
    "refresh[_-]?token",
    "client[_-]?secret",
)
# Escape and ordinary-character alternatives are disjoint: a truncated quoted
# value must not trigger exponential backtracking on attacker-controlled logs.
_QUOTED_MAPPING_PATTERN = re.compile(
    r'(?is)(["\'](?:'
    + "|".join(_QUOTED_CREDENTIAL_KEYS)
    + r')["\']\s*[:=]\s*)(["\'])((?:\\(?:.|$)|(?!\2)[^\\])*)(?:\2|$)'
)
_BARE_BEARER_PATTERN = re.compile(
    r"(?i)(?<![\w-])((?:authorization\s*[:=]\s*)?bearer\s+)([^\s,;]+)"
)
_ASSIGNMENT_PATTERN = re.compile(
    r"(?is)(?<![A-Za-z0-9_-])("
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|password|passwd|secret|token)"
    r"\s*[:=]\s*)"
    r"(?:([\"'])((?:\\(?:.|$)|(?!\2)[^\\])*)(?:\2|$)|([^\s,;]+))"
)
_HEADER_PATTERN = re.compile(
    r"(?is)((?:authorization|x-api-key|x-goog-api-key|"
    r"x-shopify-access-token)\s*[:=]\s*"
    r"(?:(?:bearer|basic)\s+)?)"
    r"""(?:(["'])(?:\\(?:.|$)|(?!\2)[^\\])*(?:\2|$)|[^\s,;]+)"""
)

_is_applied = False


def _redact_log_text(text: str) -> str:
    """Mask common Authorization/Bearer/API-key/password shapes in log text.

    Protect quoted values before the shared scrubber can truncate them at
    whitespace. The shared helper then handles URLs and prefixed credential
    keys; strict replacements finish headers and short values whose suffix
    would otherwise remain visible. Keys, schemes, and
    neighboring non-secret diagnostics are preserved. This is not general
    PII detection and does not cover unlabeled or arbitrarily encoded
    secrets.
    """
    if not text:
        return text
    redacted = _QUOTED_MAPPING_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}{match.group(2)}",
        text,
    )
    redacted = _ASSIGNMENT_PATTERN.sub(_replace_assignment, redacted)
    redacted = _HEADER_PATTERN.sub(
        lambda match: f"{match.group(1)}{_REDACTED}", redacted
    )
    redacted = redact_sensitive_text(redacted)
    redacted = _HEADER_PATTERN.sub(
        lambda match: f"{match.group(1)}{_REDACTED}",
        redacted,
    )
    redacted = _BARE_BEARER_PATTERN.sub(
        lambda match: f"{match.group(1)}{_REDACTED}",
        redacted,
    )
    redacted = _ASSIGNMENT_PATTERN.sub(_replace_assignment, redacted)
    return redacted


def _replace_assignment(match: re.Match[str]) -> str:
    prefix = match.group(1)
    quote = match.group(2)
    if quote:
        return f"{prefix}{quote}{_REDACTED}{quote}"
    return f"{prefix}{_REDACTED}"


class JsonLogFormatter(logging.Formatter):
    """Emit one compact JSON object per record with stable field names."""

    def format(self, record: logging.LogRecord) -> str:
        message = _redact_log_text(record.getMessage())
        exception: str | None = None
        if record.exc_info and record.exc_info[0] is not None:
            exception = super().formatException(record.exc_info)
        elif record.exc_text:
            exception = record.exc_text
        if record.stack_info:
            stack = super().formatStack(record.stack_info)
            exception = f"{exception}\n{stack}" if exception else stack
        if exception is not None:
            exception = _redact_log_text(exception)
        created = datetime.fromtimestamp(record.created, tz=timezone.utc)
        payload = {
            "timestamp": created.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "exception": exception,
        }
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def setup_logging(level: LogLevel | None = None, force: bool = False) -> None:
    """Configure logging for the entire application.

    Args:
        level: Log level. If None, reads from XAGENT_LOG_LEVEL env var,
               defaults to "INFO" if env var is not set or invalid.
        force: If True, reconfigure logging even if already applied.
    """
    global _is_applied
    if _is_applied and not force:
        return
    # Read log level from env var if not provided
    if level is None:
        level = cast(LogLevel, os.getenv("XAGENT_LOG_LEVEL", "INFO").upper())
    else:
        level = cast(LogLevel, level.upper())
    # Validate and fallback to INFO if invalid
    original_level = level
    if invalid_level := level not in _VALID_LEVELS:
        level = "INFO"
    # apply logging config
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "()": JsonLogFormatter,
                }
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                }
            },
            "loggers": {
                "aiohttp": {"level": "WARNING"},
                "sqlalchemy": {"level": "WARNING"},
                "urllib3": {"level": "WARNING"},
                "uvicorn.access": {"level": "WARNING"},
                "uvicorn.error": {"level": "INFO"},
                "httpx": {"level": "WARNING"},
                "httpcore": {"level": "WARNING"},
                "xagent": {"level": level},
            },
            "root": {
                "level": level,
                "handlers": ["default"],
            },
        }
    )

    if invalid_level:
        logging.warning(
            "Invalid log level '%r', falling back to 'INFO'", original_level
        )

    _is_applied = True
