# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

import base64
import logging
from collections.abc import AsyncIterator, MutableMapping
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from otcs_mcp.config import Settings, Transport
from otcs_mcp.http_app import SecurityMiddleware
from otcs_mcp.observability import Metrics, correlation_id_var
from otcs_mcp.security import SlidingWindowRateLimiter

USER, PASSWORD = "mcp", "mcp-secret-pw"
NO_AUTH: Any = None  # httpx accepts None per request to disable the client default auth
MAX_BODY = 100


class RecordingApp:
    """Minimal downstream ASGI app: records what it receives and answers 200."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.scopes: list[str] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.scopes.append(scope["type"])
        if scope["type"] != "http":
            return
        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        self.calls.append(
            {"path": scope["path"], "body": body, "correlation_id": correlation_id_var.get()}
        )
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"downstream"})


@pytest.fixture
def http_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "mcp_transport": Transport.HTTP,
            "mcp_http_auth_user": USER,
            "mcp_http_auth_password": SecretStr(PASSWORD),
            "mcp_max_body_bytes": MAX_BODY,
            "mcp_rate_limit_per_minute": 5,
            "mcp_allowed_origins": ["https://app.example.com"],
        }
    )


@pytest.fixture
def app() -> RecordingApp:
    return RecordingApp()


@pytest.fixture
def metrics() -> Metrics:
    return Metrics()


@pytest.fixture
def middleware(http_settings: Settings, app: RecordingApp, metrics: Metrics) -> SecurityMiddleware:
    return SecurityMiddleware(app, settings=http_settings, metrics=metrics)


@pytest.fixture
async def http(middleware: SecurityMiddleware) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=middleware, client=("10.0.0.7", 5555))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost", auth=(USER, PASSWORD)
    ) as client:
        yield client


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# ----------------------------------------------------------------- health


async def test_healthz_answers_200_without_credentials_and_reveals_nothing(
    http: httpx.AsyncClient, app: RecordingApp
) -> None:
    response = await http.get("/healthz", auth=NO_AUTH)

    assert response.status_code == 200
    assert response.text == "ok"
    assert app.calls == []  # served by the middleware itself


async def test_healthz_is_reachable_with_any_host_header(http: httpx.AsyncClient) -> None:
    response = await http.get("/healthz", auth=NO_AUTH, headers={"Host": "anything.example"})

    assert response.status_code == 200


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
async def test_healthz_rejects_methods_other_than_get(http: httpx.AsyncClient, method: str) -> None:
    response = await http.request(method, "/healthz", auth=NO_AUTH)

    assert response.status_code == 405
    assert response.headers["allow"] == "GET"


# ----------------------------------------------------------------- authentication


async def test_request_without_credentials_gets_401_with_basic_challenge(
    http: httpx.AsyncClient, app: RecordingApp
) -> None:
    response = await http.post("/mcp", auth=NO_AUTH)

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic realm=")
    assert response.json() == {"status": 401, "message": "Authentication required."}
    assert app.calls == []


@pytest.mark.parametrize(
    "headers",
    [
        _basic(USER, "wrong"),
        _basic("wrong", PASSWORD),
        {"Authorization": "Bearer abc"},
        {"Authorization": "Basic !!!"},
        {"Authorization": "Basic " + base64.b64encode(b"nocolon").decode()},
        {"Authorization": ""},
    ],
)
async def test_request_with_wrong_or_malformed_credentials_gets_401(
    http: httpx.AsyncClient, app: RecordingApp, headers: dict[str, str]
) -> None:
    response = await http.post("/mcp", auth=NO_AUTH, headers=headers)

    assert response.status_code == 401
    assert app.calls == []
    assert PASSWORD not in response.text


async def test_valid_credentials_reach_the_downstream_app(
    http: httpx.AsyncClient, app: RecordingApp
) -> None:
    response = await http.post("/mcp", content=b"hello")

    assert response.status_code == 200
    assert response.text == "downstream"
    assert app.calls[0]["path"] == "/mcp"
    assert app.calls[0]["body"] == b"hello"


async def test_authentication_failures_are_logged_without_credentials(
    http: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)

    await http.post("/mcp", auth=NO_AUTH, headers=_basic(USER, "guess-1234"))

    assert "authentication failed" in caplog.text
    assert "guess-1234" not in caplog.text
    assert any(getattr(r, "client", None) == "10.0.0.7" for r in caplog.records)


# ----------------------------------------------------------------- host and origin


@pytest.mark.parametrize("host", ["evil.example", "localhost.evil.io", "127.0.0.2"])
async def test_unknown_host_gets_421_even_before_authentication(
    http: httpx.AsyncClient, app: RecordingApp, host: str
) -> None:
    response = await http.post("/mcp", auth=NO_AUTH, headers={"Host": host})

    assert response.status_code == 421
    assert "MCP_ALLOWED_HOSTS" in response.json()["message"]
    assert app.calls == []


@pytest.mark.parametrize("host", ["localhost:8443", "LOCALHOST", "127.0.0.1:9", "[::1]:8443"])
async def test_allowed_hosts_are_accepted_with_or_without_port(
    http: httpx.AsyncClient, host: str
) -> None:
    response = await http.post("/mcp", headers={"Host": host})

    assert response.status_code == 200


async def test_request_without_origin_is_allowed_when_authenticated(
    http: httpx.AsyncClient,
) -> None:
    assert (await http.post("/mcp")).status_code == 200


async def test_allowed_origin_is_accepted(http: httpx.AsyncClient) -> None:
    response = await http.post("/mcp", headers={"Origin": "https://app.example.com"})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "origin",
    ["https://evil.example", "null", "http://app.example.com", "https://app.example.com.evil.io"],
)
async def test_disallowed_origin_gets_403_even_with_valid_credentials(
    http: httpx.AsyncClient, app: RecordingApp, origin: str
) -> None:
    response = await http.post("/mcp", headers={"Origin": origin})

    assert response.status_code == 403
    assert app.calls == []


async def test_any_origin_is_rejected_when_allowlist_is_empty(
    http_settings: Settings, app: RecordingApp
) -> None:
    strict = SecurityMiddleware(
        app, settings=http_settings.model_copy(update={"mcp_allowed_origins": []})
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=strict),
        base_url="http://localhost",
        auth=(USER, PASSWORD),
    ) as client:
        response = await client.post("/mcp", headers={"Origin": "https://app.example.com"})

    assert response.status_code == 403


# ----------------------------------------------------------------- rate limiting


async def test_requests_beyond_the_limit_get_429_with_retry_after(
    http: httpx.AsyncClient, app: RecordingApp
) -> None:
    statuses = [(await http.post("/mcp")).status_code for _ in range(7)]
    blocked = await http.post("/mcp")

    assert statuses == [200] * 5 + [429] * 2
    assert blocked.headers["retry-after"] == "60"
    assert len(app.calls) == 5


async def test_rate_limit_counts_unauthenticated_attempts_to_slow_down_guessing(
    http: httpx.AsyncClient,
) -> None:
    for _ in range(5):
        assert (await http.post("/mcp", auth=NO_AUTH)).status_code == 401

    assert (await http.post("/mcp", auth=NO_AUTH)).status_code == 429
    assert (await http.post("/mcp")).status_code == 429  # valid credentials share the budget


async def test_rate_limit_is_tracked_per_client_address(
    http_settings: Settings, app: RecordingApp
) -> None:
    shared = SlidingWindowRateLimiter(1)
    middleware = SecurityMiddleware(app, settings=http_settings, limiter=shared)

    async def call(address: str) -> int:
        transport = httpx.ASGITransport(app=middleware, client=(address, 1))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost", auth=(USER, PASSWORD)
        ) as client:
            return (await client.post("/mcp")).status_code

    assert [await call("1.1.1.1"), await call("2.2.2.2"), await call("1.1.1.1")] == [200, 200, 429]


async def test_health_checks_do_not_consume_the_rate_limit(http: httpx.AsyncClient) -> None:
    for _ in range(20):
        assert (await http.get("/healthz", auth=NO_AUTH)).status_code == 200

    assert (await http.post("/mcp")).status_code == 200


# ----------------------------------------------------------------- body size


async def test_declared_content_length_over_limit_gets_413_without_reaching_the_app(
    http: httpx.AsyncClient, app: RecordingApp
) -> None:
    response = await http.post("/mcp", content=b"x" * (MAX_BODY + 1))

    assert response.status_code == 413
    assert app.calls == []


async def test_body_exactly_at_the_limit_is_accepted_and_delivered_intact(
    http: httpx.AsyncClient, app: RecordingApp
) -> None:
    payload = bytes(range(100))

    response = await http.post("/mcp", content=payload)

    assert response.status_code == 200
    assert app.calls[0]["body"] == payload


async def test_chunked_body_over_limit_gets_413_even_without_content_length(
    http: httpx.AsyncClient, app: RecordingApp
) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(5):
            yield b"x" * 40

    response = await http.post("/mcp", content=chunks())

    assert response.status_code == 413
    assert app.calls == []


async def test_chunked_body_within_limit_is_reassembled_for_the_app(
    http: httpx.AsyncClient, app: RecordingApp
) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"hel"
        yield b"lo"

    assert (await http.post("/mcp", content=chunks())).status_code == 200
    assert app.calls[0]["body"] == b"hello"


async def test_get_without_body_reaches_the_app(http: httpx.AsyncClient, app: RecordingApp) -> None:
    assert (await http.get("/mcp")).status_code == 200
    assert app.calls[0]["body"] == b""


async def test_invalid_content_length_header_is_ignored_and_actual_size_is_enforced(
    middleware: SecurityMiddleware, app: RecordingApp
) -> None:
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"x" * (MAX_BODY + 5), "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    scope = _scope(headers=[(b"content-length", b"abc"), *_auth_headers()])
    await middleware(scope, receive, send)

    assert sent[0]["status"] == 413
    assert app.calls == []


# ----------------------------------------------------------------- protocol edge cases


def _auth_headers() -> list[tuple[bytes, bytes]]:
    token = base64.b64encode(f"{USER}:{PASSWORD}".encode())
    return [(b"authorization", b"Basic " + token), (b"host", b"localhost")]


def _scope(headers: list[tuple[bytes, bytes]] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": headers if headers is not None else _auth_headers(),
        "client": ("10.0.0.7", 1),
        **extra,
    }


async def test_client_disconnect_before_body_arrives_gets_no_response_and_no_app_call(
    middleware: SecurityMiddleware, app: RecordingApp
) -> None:
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    await middleware(_scope(), receive, send)

    assert sent == []
    assert app.calls == []


async def test_disconnect_after_body_is_still_passed_to_the_app(
    middleware: SecurityMiddleware,
) -> None:
    seen: list[str] = []

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        seen.append((await receive())["type"])
        seen.append((await receive())["type"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    guarded = SecurityMiddleware(downstream, settings=middleware._settings)
    queue: list[dict[str, Any]] = [
        {"type": "http.request", "body": b"{}", "more_body": False},
        {"type": "http.disconnect"},
    ]

    async def receive() -> dict[str, Any]:
        return queue.pop(0)

    async def send(message: MutableMapping[str, Any]) -> None:
        return None

    await guarded(_scope(), receive, send)

    assert seen == ["http.request", "http.disconnect"]


@pytest.mark.parametrize("scope_type", ["lifespan", "websocket"])
async def test_non_http_scopes_pass_through_untouched(
    middleware: SecurityMiddleware, app: RecordingApp, scope_type: str
) -> None:
    async def receive() -> dict[str, Any]:
        return {"type": "lifespan.startup"}

    async def send(message: MutableMapping[str, Any]) -> None:
        return None

    await middleware({"type": scope_type}, receive, send)

    assert app.scopes == [scope_type]


# ----------------------------------------------------------------- correlation id and logging


async def test_response_carries_a_generated_request_id_and_app_sees_the_same_one(
    http: httpx.AsyncClient, app: RecordingApp
) -> None:
    response = await http.post("/mcp")

    request_id = response.headers["x-request-id"]
    assert len(request_id) == 32
    assert app.calls[0]["correlation_id"] == request_id
    assert correlation_id_var.get() is None  # reset after the request


async def test_valid_client_request_id_is_echoed(http: httpx.AsyncClient) -> None:
    response = await http.post("/mcp", headers={"X-Request-ID": "trace-abc_123.9"})

    assert response.headers["x-request-id"] == "trace-abc_123.9"


@pytest.mark.parametrize("supplied", ["bad id with spaces", "x" * 65, "a;b", "<script>"])
async def test_unsafe_client_request_ids_are_replaced(
    http: httpx.AsyncClient, supplied: str
) -> None:
    response = await http.post("/mcp", headers={"X-Request-ID": supplied})

    assert response.headers["x-request-id"] != supplied
    assert len(response.headers["x-request-id"]) == 32


async def test_error_responses_also_carry_the_request_id(http: httpx.AsyncClient) -> None:
    response = await http.post("/mcp", auth=NO_AUTH)

    assert response.status_code == 401
    assert response.headers["x-request-id"]


async def test_each_request_is_logged_with_method_path_status_and_client(
    http: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    await http.post("/mcp")

    record = next(r for r in caplog.records if r.getMessage() == "request")
    assert (record.method, record.path, record.status, record.client) == (  # type: ignore[attr-defined]
        "POST",
        "/mcp",
        200,
        "10.0.0.7",
    )


async def test_error_responses_do_not_leak_internals_and_disable_caching(
    http: httpx.AsyncClient,
) -> None:
    response = await http.post("/mcp", auth=NO_AUTH)

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    for secret in (PASSWORD, "otcs.example.com", "Traceback"):
        assert secret not in response.text


async def test_missing_client_address_is_handled(
    middleware: SecurityMiddleware, app: RecordingApp
) -> None:
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    await middleware(_scope(client=None), receive, send)

    assert sent[0]["status"] == 200


# ----------------------------------------------------------------- metrics


async def test_http_responses_are_counted_by_status_class(
    http: httpx.AsyncClient, metrics: Metrics
) -> None:
    await http.post("/mcp")
    await http.post("/mcp", auth=NO_AUTH)
    await http.get("/healthz", auth=NO_AUTH)

    text = metrics.render()
    assert 'otcs_mcp_http_responses_total{status="2xx"} 2' in text
    assert 'otcs_mcp_http_responses_total{status="4xx"} 1' in text
