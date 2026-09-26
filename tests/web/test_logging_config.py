"""Regression tests for structured JSON logging from ``setup_logging``.

These tests drive the public ``setup_logging`` entrypoint and parse the JSON
objects written to stderr. They restore the process logging configuration
after each case because ``dictConfig`` closes previously registered
handlers globally.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator
from contextlib import redirect_stderr
from typing import Any

import pytest

from xagent.web import logging_config as logging_config_module
from xagent.web.logging_config import setup_logging


class _LoggingSnapshot:
    """Process-global logging state that ``dictConfig`` can destroy."""

    def __init__(self) -> None:
        root = logging.getLogger()
        self.root_level = root.level
        self.root_disabled = root.disabled
        self.root_propagate = root.propagate
        self.root_filters = list(root.filters)
        self.root_handlers = list(root.handlers)
        self.handler_list = list(logging._handlerList)
        self.named_handlers = dict(logging._handlers)
        self.logger_dict = dict(root.manager.loggerDict)
        self.loggers: dict[str, tuple[Any, ...]] = {}
        for name, logger in self.logger_dict.items():
            if not isinstance(logger, logging.Logger):
                continue
            self.loggers[name] = (
                logger.level,
                logger.disabled,
                logger.propagate,
                list(logger.handlers),
                list(logger.filters),
            )


@pytest.fixture
def restore_logging(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Restore logging after ``setup_logging`` mutates the process."""
    snapshot = _LoggingSnapshot()
    applied = logging_config_module._is_applied
    # dictConfig closes every registered handler, including pytest's log file.
    # Protect test-runner resources, but still close handlers created by the SUT.
    protected = {ref() for ref in snapshot.handler_list if ref() is not None}
    original_shutdown = logging.shutdown

    def close_test_handlers(handler_list=None) -> None:
        references = logging._handlerList if handler_list is None else handler_list
        original_shutdown([ref for ref in references if ref() not in protected])

    monkeypatch.setattr(logging, "shutdown", close_test_handlers)
    try:
        yield
    finally:
        logging_config_module._is_applied = applied
        root = logging.getLogger()
        created_handlers = [
            handler
            for handler in root.handlers
            if handler not in snapshot.root_handlers
        ]
        for name, state in snapshot.loggers.items():
            restored = logging.getLogger(name)
            restored.level, restored.disabled, restored.propagate, handlers, filters = (
                state
            )
            restored.handlers[:] = handlers
            restored.filters[:] = filters
        root.handlers[:] = snapshot.root_handlers
        root.level = snapshot.root_level
        root.disabled = snapshot.root_disabled
        root.propagate = snapshot.root_propagate
        root.filters[:] = snapshot.root_filters
        logging._handlerList[:] = snapshot.handler_list
        logging._handlers.clear()
        logging._handlers.update(snapshot.named_handlers)
        root.manager.loggerDict.clear()
        root.manager.loggerDict.update(snapshot.logger_dict)
        for handler in created_handlers:
            handler.close()


def _capture_setup(*args: Any, **kwargs: Any) -> io.StringIO:
    stream = io.StringIO()
    with redirect_stderr(stream):
        setup_logging(*args, **kwargs)
    return stream


def _parse_records(stream: io.StringIO) -> list[dict[str, Any]]:
    lines = [line for line in stream.getvalue().splitlines() if line]
    records = [json.loads(line) for line in lines]
    for record in records:
        assert set(record) == {"timestamp", "level", "logger", "message", "exception"}
        assert isinstance(record["timestamp"], str)
        assert record["timestamp"].endswith("Z")
        assert len(record["timestamp"]) == 24
        assert record["timestamp"][10] == "T"
        assert record["timestamp"][19] == "."
    return records


def test_emits_json_with_stable_fields_and_info_level(restore_logging) -> None:
    stream = _capture_setup(level="INFO", force=True)
    logging.getLogger("xagent.web.logging_config").info("ready")
    records = _parse_records(stream)
    assert len(records) == 1
    record = records[0]
    assert record["level"] == "INFO"
    assert record["logger"] == "xagent.web.logging_config"
    assert record["message"] == "ready"
    assert record["exception"] is None


def test_multiline_unicode_message_stays_one_json_record(restore_logging) -> None:
    stream = _capture_setup(level="INFO", force=True)
    logging.getLogger("xagent.test").warning("line one\nline two 😀")
    records = _parse_records(stream)
    assert len(records) == 1
    assert records[0]["message"] == "line one\nline two 😀"
    assert records[0]["level"] == "WARNING"


def test_debug_messages_are_filtered_at_info(restore_logging) -> None:
    stream = _capture_setup(level="INFO", force=True)
    logger = logging.getLogger("xagent.test")
    logger.debug("hidden")
    logger.info("visible")
    records = _parse_records(stream)
    assert [record["message"] for record in records] == ["visible"]
    assert records[0]["level"] == "INFO"


def test_explicit_debug_level_emits_debug(restore_logging) -> None:
    stream = _capture_setup(level="DEBUG", force=True)
    logging.getLogger("xagent.test").debug("verbose")
    records = _parse_records(stream)
    assert records[0]["level"] == "DEBUG"
    assert records[0]["message"] == "verbose"


def test_environment_level_is_used_when_level_is_omitted(
    restore_logging, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAGENT_LOG_LEVEL", "ERROR")
    stream = _capture_setup(force=True)
    logger = logging.getLogger("xagent.test")
    logger.warning("ignored")
    logger.error("kept")
    records = _parse_records(stream)
    assert [record["message"] for record in records] == ["kept"]
    assert records[0]["level"] == "ERROR"


def test_explicit_level_overrides_environment(
    restore_logging, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAGENT_LOG_LEVEL", "ERROR")
    stream = _capture_setup(level="DEBUG", force=True)
    logging.getLogger("xagent.test").debug("from-arg")
    records = _parse_records(stream)
    assert records[0]["message"] == "from-arg"


def test_setup_logging_is_idempotent_without_force(restore_logging) -> None:
    first = _capture_setup(level="WARNING", force=True)
    logging.getLogger("xagent.test").warning("before")
    second = _capture_setup(level="DEBUG")
    logging.getLogger("xagent.test").debug("after-idempotent")
    logging.getLogger("xagent.test").warning("still-warning")
    assert _parse_records(second) == []
    records = _parse_records(first)
    assert [record["message"] for record in records] == ["before", "still-warning"]


def test_force_reconfigures_level(restore_logging) -> None:
    _capture_setup(level="ERROR", force=True)
    stream = _capture_setup(level="DEBUG", force=True)
    logging.getLogger("xagent.test").debug("forced")
    records = _parse_records(stream)
    assert records[0]["message"] == "forced"
    assert records[0]["level"] == "DEBUG"


def test_invalid_level_warns_and_falls_back_to_info(restore_logging) -> None:
    stream = _capture_setup(level="NOPE", force=True)
    logging.getLogger("xagent.test").info("fallback-visible")
    logging.getLogger("xagent.test").debug("fallback-hidden")
    records = _parse_records(stream)
    assert [record["level"] for record in records] == ["WARNING", "INFO"]
    warning = records[0]
    assert warning["logger"] == "root"
    assert "NOPE" in warning["message"]
    assert "INFO" in warning["message"]
    assert records[1]["message"] == "fallback-visible"


def test_exception_chain_is_recorded_and_redacted(restore_logging) -> None:
    stream = _capture_setup(level="ERROR", force=True)
    try:
        try:
            raise ValueError("inner api_key=sk-super-secret")
        except ValueError as inner:
            raise RuntimeError("outer timeout=30") from inner
    except RuntimeError:
        logging.getLogger("xagent.test").exception("failed request")
    records = _parse_records(stream)
    assert len(records) == 1
    record = records[0]
    assert record["level"] == "ERROR"
    assert record["message"] == "failed request"
    exception = record["exception"]
    assert exception is not None
    assert "ValueError" in exception
    assert "RuntimeError" in exception
    assert "timeout=30" in exception
    assert "sk-super-secret" not in exception
    assert "api_key=" in exception
    assert "[REDACTED]" in exception
    assert "Traceback (most recent call last)" in exception


@pytest.mark.parametrize(
    ("message", "secret", "preserved"),
    [
        (
            "Authorization: Bearer sk-secret-value timeout=30",
            "sk-secret-value",
            "timeout=30",
        ),
        (
            "Authorization: Basic dXNlcjpzdXBlci1zZWNyZXQtdG9rZW4= host=db",
            "dXNlcjpzdXBlci1zZWNyZXQtdG9rZW4=",
            "host=db",
        ),
        ("Bearer sk-secret-value path=/health", "sk-secret-value", "path=/health"),
        ("api_key=sk-super-secret timeout=30", "sk-super-secret", "timeout=30"),
        ("password=hunter2 user=alice", "hunter2", "user=alice"),
        (
            '{"password": "quoted secret with spaces"} ok=true',  # pragma: allowlist secret - redaction sentinel
            "quoted secret with spaces",
            "ok=true",
        ),
        ("password=ab retry=1", "ab", "retry=1"),
    ],
)
def test_common_credential_patterns_are_redacted(
    restore_logging, message: str, secret: str, preserved: str
) -> None:
    stream = _capture_setup(level="INFO", force=True)
    logging.getLogger("xagent.test").info(message)
    records = _parse_records(stream)
    rendered = records[0]["message"]
    assert secret not in rendered
    assert preserved in rendered
    assert "[REDACTED]" in rendered


def test_noisy_library_loggers_stay_at_warning(restore_logging) -> None:
    stream = _capture_setup(level="DEBUG", force=True)
    logging.getLogger("httpx").info("library-info")
    logging.getLogger("httpx").warning("library-warning")
    records = _parse_records(stream)
    assert [record["message"] for record in records] == ["library-warning"]


@pytest.mark.timeout(2)
def test_truncated_escaped_credential_is_redacted_without_stalling(
    restore_logging,
) -> None:
    stream = _capture_setup(level="INFO", force=True)
    secret = (
        "truncated-sensitive-value"  # pragma: allowlist secret - redaction sentinel
    )
    logging.getLogger("xagent.test").warning('"password": "' + secret + "\\" * 80)
    record = _parse_records(stream)[0]
    assert secret not in record["message"]
    assert '"password": "[REDACTED]"' in record["message"]


def test_quoted_assignment_keeps_no_partial_secret(restore_logging) -> None:
    stream = _capture_setup(level="INFO", force=True)
    logging.getLogger("xagent.test").warning(
        'password="alpha beta" retry=1'  # pragma: allowlist secret - redaction sentinel
    )
    message = _parse_records(stream)[0]["message"]
    assert "alpha" not in message
    assert "beta" not in message
    assert "retry=1" in message


def test_quoted_authorization_value_does_not_leave_token(restore_logging) -> None:
    stream = _capture_setup(level="INFO", force=True)
    logging.getLogger("xagent.test").warning(
        "Authorization: 'Bearer sk-secret' status=401"
    )
    message = _parse_records(stream)[0]["message"]
    assert "sk-secret" not in message
    assert "status=401" in message
