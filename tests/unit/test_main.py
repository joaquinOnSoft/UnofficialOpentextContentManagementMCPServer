# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import fastmcp
import pytest
import uvicorn

from otcs_mcp import __main__ as entrypoint

ENV = {
    "OTCS_BASE_URL": "https://otcs.example.com/cs/cs",
    "OTCS_USERNAME": "svc",
    "OTCS_PASSWORD": "pw-not-to-leak",
}
HTTP_ENV = {
    **ENV,
    "MCP_TRANSPORT": "http",
    "MCP_HTTP_AUTH_USER": "http-user",
    "MCP_HTTP_AUTH_PASSWORD": "http-pass-1234",
}


class StubServer:
    def __init__(self) -> None:
        self.run_kwargs: dict[str, Any] | None = None

    def run(self, **kwargs: Any) -> None:
        self.run_kwargs = kwargs


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)  # no real .env can be picked up
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace everything that would open sockets or servers."""
    captured: dict[str, Any] = {"server": StubServer(), "uvicorn": None, "build_server": None}

    def fake_build_server(settings: Any, **kwargs: Any) -> StubServer:
        captured["build_server"] = kwargs
        return captured["server"]  # type: ignore[no-any-return]

    def fake_uvicorn_run(app: Any, **kwargs: Any) -> None:
        captured["uvicorn"] = {"app": app, **kwargs}

    monkeypatch.setattr(entrypoint, "build_server", fake_build_server)
    monkeypatch.setattr(entrypoint, "build_http_app", lambda settings, mcp, metrics: "app")
    monkeypatch.setattr(entrypoint, "OtcsClient", lambda settings, metrics: "client")
    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)
    monkeypatch.setattr(fastmcp.settings, "check_for_updates", "stable")
    return captured


def _set(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def _tls_files(tmp_path: Path, mode: int = 0o600) -> dict[str, str]:
    cert, key = tmp_path / "server.crt", tmp_path / "server.key"
    cert.write_text("cert")
    key.write_text("key")
    key.chmod(mode)
    return {"TLS_CERT_FILE": str(cert), "TLS_KEY_FILE": str(key)}


def test_main_exits_with_code_2_and_clear_message_when_configuration_is_missing(
    capsys: pytest.CaptureFixture[str], stubs: dict[str, Any]
) -> None:
    with pytest.raises(SystemExit) as info:
        entrypoint.main()

    captured = capsys.readouterr()
    assert info.value.code == 2
    assert "OTCS_BASE_URL" in captured.err
    assert captured.out == ""
    assert stubs["uvicorn"] is None


def test_main_runs_stdio_transport_keeps_stdout_clean_and_logs_warnings(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stubs: dict[str, Any],
) -> None:
    _set(monkeypatch, {**ENV, "MCP_READ_ONLY": "false", "OTCS_VERIFY_SSL": "false"})

    entrypoint.main()

    assert stubs["server"].run_kwargs == {"transport": "stdio", "show_banner": False}
    assert stubs["uvicorn"] is None
    assert fastmcp.settings.check_for_updates == "off"  # no outbound call to PyPI
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout is reserved for the MCP protocol
    assert "pw-not-to-leak" not in captured.err
    assert "MCP_READ_ONLY=false" in captured.err
    assert "OTCS_VERIFY_SSL=false" in captured.err


def test_main_shares_one_metrics_registry_with_the_server_when_stdio_starts(
    monkeypatch: pytest.MonkeyPatch, stubs: dict[str, Any]
) -> None:
    _set(monkeypatch, ENV)

    entrypoint.main()

    assert stubs["build_server"]["client"] == "client"
    assert stubs["build_server"]["metrics"] is not None


def test_main_serves_https_with_certificate_and_hardened_uvicorn_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stubs: dict[str, Any]
) -> None:
    tls = _tls_files(tmp_path)
    _set(monkeypatch, {**HTTP_ENV, **tls, "MCP_HOST": "127.0.0.1", "MCP_PORT": "9443"})

    entrypoint.main()

    options = stubs["uvicorn"]
    assert options["app"] == "app"
    assert (options["host"], options["port"]) == ("127.0.0.1", 9443)
    assert options["ssl_certfile"] == tls["TLS_CERT_FILE"]
    assert options["ssl_keyfile"] == tls["TLS_KEY_FILE"]
    assert options["log_config"] is None
    assert options["access_log"] is False
    assert options["server_header"] is False
    assert stubs["server"].run_kwargs is None  # stdio was not started


def test_main_serves_plain_http_without_ssl_options_when_tls_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stubs: dict[str, Any],
) -> None:
    _set(monkeypatch, {**HTTP_ENV, "TLS_ENABLED": "false"})

    entrypoint.main()

    assert "ssl_certfile" not in stubs["uvicorn"]
    assert "unencrypted" in capsys.readouterr().err


def test_main_exits_with_code_2_when_tls_files_are_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    stubs: dict[str, Any],
) -> None:
    _set(
        monkeypatch,
        {
            **HTTP_ENV,
            "TLS_CERT_FILE": str(tmp_path / "no.crt"),
            "TLS_KEY_FILE": str(tmp_path / "no.key"),
        },
    )

    with pytest.raises(SystemExit) as info:
        entrypoint.main()

    assert info.value.code == 2
    assert "TLS_CERT_FILE" in capsys.readouterr().err
    assert stubs["uvicorn"] is None


def test_main_warns_when_private_key_is_readable_by_others(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    stubs: dict[str, Any],
) -> None:
    _set(monkeypatch, {**HTTP_ENV, **_tls_files(tmp_path, mode=0o644)})

    entrypoint.main()

    assert "0600" in capsys.readouterr().err
    assert stubs["uvicorn"] is not None


def test_main_writes_structured_json_logs_and_masks_secrets_when_http_starts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    stubs: dict[str, Any],
) -> None:
    _set(
        monkeypatch,
        {**HTTP_ENV, **_tls_files(tmp_path), "TLS_ENABLED": "false", "LOG_LEVEL": "DEBUG"},
    )
    entrypoint.main()
    logging.getLogger("probe").info("passwords: pw-not-to-leak http-pass-1234")

    err = capsys.readouterr().err
    assert '"level": "INFO"' in err
    assert "pw-not-to-leak" not in err
    assert "http-pass-1234" not in err
    assert "passwords: *** ***" in err
