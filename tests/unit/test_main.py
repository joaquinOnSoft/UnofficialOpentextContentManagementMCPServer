# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

import logging
from pathlib import Path
from typing import Any

import fastmcp
import pytest

from otcs_mcp import __main__ as entrypoint

ENV = {
    "OTCS_BASE_URL": "https://otcs.example.com/cs/cs",
    "OTCS_USERNAME": "svc",
    "OTCS_PASSWORD": "pw-not-to-leak",
}


class StubServer:
    def __init__(self) -> None:
        self.run_kwargs: dict[str, Any] | None = None

    def run(self, **kwargs: Any) -> None:
        self.run_kwargs = kwargs


@pytest.fixture(autouse=True)
def _isolated_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no real .env can be picked up


def test_main_exits_with_code_2_and_clear_message_when_configuration_is_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as info:
        entrypoint.main()

    captured = capsys.readouterr()
    assert info.value.code == 2
    assert "OTCS_BASE_URL" in captured.err
    assert captured.out == ""


def test_main_refuses_http_transport_until_it_is_implemented(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key, value in {
        **ENV,
        "MCP_TRANSPORT": "http",
        "MCP_HTTP_AUTH_USER": "u",
        "MCP_HTTP_AUTH_PASSWORD": "p",
    }.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(SystemExit) as info:
        entrypoint.main()

    assert info.value.code == 2
    assert "phase 4" in capsys.readouterr().err


def test_main_runs_stdio_transport_keeps_stdout_clean_and_logs_warnings(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    for key, value in {**ENV, "MCP_READ_ONLY": "false", "OTCS_VERIFY_SSL": "false"}.items():
        monkeypatch.setenv(key, value)
    stub = StubServer()
    monkeypatch.setattr(entrypoint, "build_server", lambda settings: stub)
    monkeypatch.setattr(fastmcp.settings, "check_for_updates", "stable")
    caplog.set_level(logging.WARNING)

    entrypoint.main()

    assert stub.run_kwargs == {"transport": "stdio", "show_banner": False}
    assert fastmcp.settings.check_for_updates == "off"  # no outbound call to PyPI
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout is reserved for the MCP protocol
    assert "pw-not-to-leak" not in captured.err + caplog.text
    assert "MCP_READ_ONLY=false" in caplog.text
    assert "OTCS_VERIFY_SSL=false" in caplog.text
