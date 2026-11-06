# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

import io
import json
import logging
from collections.abc import Iterator

import pytest
from pydantic import SecretStr

from otcs_mcp.config import LogFormat, Settings
from otcs_mcp.logging_setup import RedactingFilter, configure_logging
from otcs_mcp.observability import correlation_id_var

OTCS_PW = "otcs-secret-pw"
HTTP_PW = "http-secret-pw"


@pytest.fixture(autouse=True)
def _restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture(autouse=True)
def _restore_framework_logger() -> Iterator[None]:
    framework = logging.getLogger("fastmcp")
    handlers, propagate, level = list(framework.handlers), framework.propagate, framework.level
    yield
    framework.handlers[:] = handlers
    framework.propagate = propagate
    framework.setLevel(level)


@pytest.fixture
def log_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "otcs_password": SecretStr(OTCS_PW),
            "mcp_http_auth_password": SecretStr(HTTP_PW),
        }
    )


def _lines(stream: io.StringIO) -> list[str]:
    return [line for line in stream.getvalue().splitlines() if line]


def test_json_logs_have_one_object_per_line_with_standard_fields(
    log_settings: Settings,
) -> None:
    stream = io.StringIO()
    configure_logging(log_settings, stream=stream)

    logging.getLogger("demo").warning("hello %s", "world")

    record = json.loads(_lines(stream)[0])
    assert record["level"] == "WARNING"
    assert record["logger"] == "demo"
    assert record["message"] == "hello world"
    assert record["ts"].endswith("+00:00")
    assert "correlation_id" not in record


def test_json_logs_include_correlation_id_and_extra_fields_when_present(
    log_settings: Settings,
) -> None:
    stream = io.StringIO()
    configure_logging(log_settings, stream=stream)
    token = correlation_id_var.set("req-123")
    try:
        logging.getLogger("demo").info(
            "request", extra={"method": "POST", "status": 200, "unlisted": "dropped"}
        )
    finally:
        correlation_id_var.reset(token)

    record = json.loads(_lines(stream)[0])
    assert record["correlation_id"] == "req-123"
    assert (record["method"], record["status"]) == ("POST", 200)
    assert "unlisted" not in record


def test_secrets_are_masked_in_messages_and_format_arguments(log_settings: Settings) -> None:
    stream = io.StringIO()
    configure_logging(log_settings, stream=stream)

    logging.getLogger("demo").error("login with %s failed; http=%s", OTCS_PW, HTTP_PW)

    output = stream.getvalue()
    assert OTCS_PW not in output
    assert HTTP_PW not in output
    assert "login with *** failed; http=***" in output


def test_secrets_are_masked_in_exception_tracebacks_and_type_is_kept(
    log_settings: Settings,
) -> None:
    stream = io.StringIO()
    configure_logging(log_settings, stream=stream)

    try:
        raise RuntimeError(f"cannot authenticate with {OTCS_PW}")
    except RuntimeError:
        logging.getLogger("demo").exception("boom")

    output = stream.getvalue()
    record = json.loads(_lines(stream)[0])
    assert OTCS_PW not in output
    assert record["exc_type"] == "RuntimeError"
    assert "cannot authenticate with ***" in record["exception"]


def test_text_format_is_used_when_requested_and_still_masks_secrets(
    log_settings: Settings,
) -> None:
    stream = io.StringIO()
    configure_logging(log_settings.model_copy(update={"log_format": LogFormat.TEXT}), stream=stream)
    token = correlation_id_var.set("req-9")
    try:
        logging.getLogger("demo").warning("pw=%s", OTCS_PW)
    finally:
        correlation_id_var.reset(token)

    line = _lines(stream)[0]
    assert "WARNING demo: pw=***" in line
    assert line.endswith("[req-9]")


def test_text_format_has_no_suffix_without_correlation_id(log_settings: Settings) -> None:
    stream = io.StringIO()
    configure_logging(log_settings.model_copy(update={"log_format": LogFormat.TEXT}), stream=stream)

    logging.getLogger("demo").warning("plain")

    assert not _lines(stream)[0].endswith("]")


def test_configuration_is_idempotent_and_never_duplicates_handlers(log_settings: Settings) -> None:
    configure_logging(log_settings, stream=io.StringIO())
    stream = io.StringIO()
    configure_logging(log_settings, stream=stream)

    logging.getLogger("demo").warning("once")

    ours = [h for h in logging.getLogger().handlers if getattr(h, "_otcs_mcp", False)]
    assert len(ours) == 1
    assert len(_lines(stream)) == 1


def test_log_level_is_applied_to_the_root_logger(log_settings: Settings) -> None:
    stream = io.StringIO()
    configure_logging(log_settings, stream=stream)

    logging.getLogger("demo").debug("hidden")
    logging.getLogger("demo").info("shown")

    assert [json.loads(line)["message"] for line in _lines(stream)] == ["shown"]


def test_logs_go_to_stderr_by_default_and_never_to_stdout(
    log_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(log_settings)

    logging.getLogger("demo").warning("to stderr")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "to stderr" in captured.err


def test_missing_http_password_is_handled(log_settings: Settings) -> None:
    stream = io.StringIO()
    configure_logging(
        log_settings.model_copy(update={"mcp_http_auth_password": None}), stream=stream
    )

    logging.getLogger("demo").warning("pw %s", OTCS_PW)

    assert OTCS_PW not in stream.getvalue()


@pytest.mark.parametrize("secret", ["", "ab", "abc"])
def test_very_short_secrets_are_not_masked_to_avoid_mangling_ordinary_text(secret: str) -> None:
    redactor = RedactingFilter([secret])

    assert redactor.redact("cabinet abc ab") == "cabinet abc ab"


def test_longest_secret_is_masked_first_when_one_contains_another() -> None:
    redactor = RedactingFilter(["pass", "pass-extended"])

    assert redactor.redact("x pass-extended y") == "x *** y"


def test_filter_masks_an_exception_text_that_was_already_rendered() -> None:
    redactor = RedactingFilter(["hunter22"])
    record = logging.makeLogRecord({"msg": "m", "exc_text": "Traceback ... hunter22"})

    redactor.filter(record)

    assert record.exc_text == "Traceback ... ***"


def test_framework_logger_is_adopted_so_its_messages_are_json_and_redacted(
    log_settings: Settings,
) -> None:
    framework = logging.getLogger("fastmcp")
    foreign = logging.StreamHandler(io.StringIO())
    framework.addHandler(foreign)
    framework.propagate = False
    stream = io.StringIO()

    configure_logging(log_settings, stream=stream)
    logging.getLogger("fastmcp.server.server").error("failed for %s", OTCS_PW)

    assert foreign not in framework.handlers
    assert framework.propagate is True
    record = json.loads(_lines(stream)[0])
    assert record["logger"] == "fastmcp.server.server"
    assert record["message"] == "failed for ***"
    assert OTCS_PW not in stream.getvalue()


def test_filter_renders_and_masks_tracebacks_that_no_other_handler_formatted() -> None:
    redactor = RedactingFilter(["hunter22"])
    try:
        raise ValueError("token hunter22 rejected")
    except ValueError:
        import sys

        record = logging.makeLogRecord({"msg": "boom", "exc_info": sys.exc_info()})

    redactor.filter(record)

    assert record.exc_text is not None
    assert "hunter22" not in record.exc_text
    assert "token *** rejected" in record.exc_text


def test_audit_fields_of_write_operations_appear_in_the_json_log(log_settings: Settings) -> None:
    stream = io.StringIO()
    configure_logging(log_settings, stream=stream)

    logging.getLogger("demo").info(
        "write operation",
        extra={"operation": "create_folder", "target_id": 9001, "folder_title": "dropped"},
    )

    record = json.loads(_lines(stream)[0])
    assert (record["operation"], record["target_id"]) == ("create_folder", 9001)
    assert "folder_title" not in record
