# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

import http.client
import ssl
from typing import Any, ClassVar

import pytest

from otcs_mcp import healthcheck


class FakeConnection:
    """Stands in for http.client connections: no socket is ever opened."""

    instances: ClassVar[list["FakeConnection"]] = []
    status = 200
    error: Exception | None = None

    def __init__(self, host: str, port: int, **kwargs: Any) -> None:
        self.host, self.port, self.kwargs = host, port, kwargs
        self.closed = False
        self.requested: tuple[str, str, dict[str, str]] | None = None
        FakeConnection.instances.append(self)

    def request(self, method: str, path: str, headers: dict[str, str]) -> None:
        if FakeConnection.error:
            raise FakeConnection.error
        self.requested = (method, path, headers)

    def getresponse(self) -> Any:
        return type("Response", (), {"status": FakeConnection.status})()

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _fake_http(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeConnection.instances = []
    FakeConnection.status = 200
    FakeConnection.error = None
    monkeypatch.setattr(http.client, "HTTPSConnection", FakeConnection)
    monkeypatch.setattr(http.client, "HTTPConnection", FakeConnection)
    for name in ("MCP_PORT", "TLS_ENABLED"):
        monkeypatch.delenv(name, raising=False)


def test_probe_requests_healthz_on_loopback_over_https_without_verifying_the_certificate() -> None:
    assert healthcheck.probe(8443, use_tls=True) is True

    connection = FakeConnection.instances[0]
    assert (connection.host, connection.port) == ("127.0.0.1", 8443)
    assert connection.requested == ("GET", "/healthz", {"Host": "localhost"})
    context = connection.kwargs["context"]
    assert context.verify_mode == ssl.CERT_NONE
    assert context.check_hostname is False
    assert connection.closed


def test_probe_uses_plain_http_when_tls_is_off() -> None:
    assert healthcheck.probe(8080, use_tls=False) is True

    assert "context" not in FakeConnection.instances[0].kwargs


@pytest.mark.parametrize("status", [401, 404, 500, 503])
def test_probe_fails_when_server_does_not_answer_200(status: int) -> None:
    FakeConnection.status = status

    assert healthcheck.probe(8443, use_tls=True) is False
    assert FakeConnection.instances[0].closed


@pytest.mark.parametrize(
    "error",
    [
        ConnectionRefusedError("refused"),
        TimeoutError("slow"),
        http.client.BadStatusLine("x"),
        ssl.SSLError("tls"),
    ],
)
def test_probe_fails_instead_of_raising_when_the_connection_breaks(error: Exception) -> None:
    FakeConnection.error = error

    assert healthcheck.probe(8443, use_tls=True) is False
    assert FakeConnection.instances[0].closed


def test_main_defaults_to_port_8443_with_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    assert healthcheck.main() == 0

    connection = FakeConnection.instances[0]
    assert connection.port == 8443
    assert "context" in connection.kwargs


@pytest.mark.parametrize("flag", ["false", "FALSE", "0", "no", "off"])
def test_main_probes_over_plain_http_when_tls_is_disabled(
    monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    monkeypatch.setenv("TLS_ENABLED", flag)
    monkeypatch.setenv("MCP_PORT", "9000")

    assert healthcheck.main() == 0

    connection = FakeConnection.instances[0]
    assert connection.port == 9000
    assert "context" not in connection.kwargs


def test_main_returns_1_when_the_server_is_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeConnection.status = 500

    assert healthcheck.main() == 1


@pytest.mark.parametrize("port", ["abc", "", "80.5"])
def test_main_returns_1_without_probing_when_port_is_not_a_number(
    monkeypatch: pytest.MonkeyPatch, port: str
) -> None:
    monkeypatch.setenv("MCP_PORT", port)

    assert healthcheck.main() == 1
    assert FakeConnection.instances == []
