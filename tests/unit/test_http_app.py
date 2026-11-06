# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""The real FastMCP app behind the security middleware, still without any real network."""

import json
import logging
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from starlette.testclient import TestClient

from otcs_mcp.client import OtcsClient
from otcs_mcp.config import Settings, Transport
from otcs_mcp.http_app import build_http_app
from otcs_mcp.observability import Metrics, correlation_id_var
from otcs_mcp.server import ALL_TOOL_NAMES, TOOL_NAMES, build_server
from tests import payloads
from tests.conftest import Backend

AUTH = ("mcp", "mcp-secret-pw")
META: dict[str, Any] = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
}
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


class _Capture(logging.Handler):
    """Record the correlation id that is active at the moment a record is emitted."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, str | None]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.rows.append((record.getMessage(), correlation_id_var.get()))


class Harness:
    def __init__(self, settings: Settings) -> None:
        self.backend = Backend()
        self.metrics = Metrics()
        client = OtcsClient(
            settings,
            transport=httpx.MockTransport(self.backend),
            metrics=self.metrics,
        )
        self.mcp = build_server(settings, client=client, metrics=self.metrics)
        self.app = build_http_app(settings, self.mcp, metrics=self.metrics)


@pytest.fixture
def http_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "mcp_transport": Transport.HTTP,
            "mcp_http_auth_user": AUTH[0],
            "mcp_http_auth_password": SecretStr(AUTH[1]),
            "mcp_rate_limit_per_minute": 0,
            "mcp_metrics_enabled": True,
            "otcs_max_retries": 0,
        }
    )


@pytest.fixture
def harness(http_settings: Settings) -> Harness:
    return Harness(http_settings)


@pytest.fixture
def web(harness: Harness) -> Iterator[TestClient]:
    with TestClient(harness.app, base_url="http://localhost") as client:
        yield client


def rpc(
    web: TestClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    name: str | None = None,
    auth: Any = AUTH,
    meta: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    all_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
        **({"Mcp-Name": name} if name else {}),
        **(headers or {}),
    }
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {**(params or {}), "_meta": {**META, **(meta or {})}},
    }
    return web.post("/mcp", headers=all_headers, content=json.dumps(body), auth=auth)


def result_of(response: Any) -> dict[str, Any]:
    match = re.search(r"\{.*\}", response.text, re.S)
    assert match, response.text
    return dict(json.loads(match.group(0))["result"])


# ----------------------------------------------------------------- access control


def test_mcp_endpoint_requires_credentials(web: TestClient) -> None:
    response = rpc(web, "tools/list", auth=None)

    assert response.status_code == 401
    assert "www-authenticate" in response.headers


def test_mcp_endpoint_rejects_wrong_password(web: TestClient) -> None:
    assert rpc(web, "tools/list", auth=(AUTH[0], "nope")).status_code == 401


def test_healthz_is_open_and_minimal(web: TestClient) -> None:
    response = web.get("/healthz")

    assert (response.status_code, response.text) == (200, "ok")


def test_unknown_host_is_rejected_before_authentication(web: TestClient) -> None:
    assert rpc(web, "tools/list", headers={"Host": "evil.example"}).status_code == 421


def test_foreign_origin_is_rejected_even_with_credentials(web: TestClient) -> None:
    assert rpc(web, "tools/list", headers={"Origin": "https://evil.example"}).status_code == 403


def test_oversized_body_is_rejected_with_413(web: TestClient, http_settings: Settings) -> None:
    big = "x" * (http_settings.mcp_max_body_bytes + 1)

    response = web.post(
        "/mcp", content=big, auth=AUTH, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 413


# ----------------------------------------------------------------- protocol over HTTP


def test_server_discover_works_over_http_with_the_2026_07_28_protocol(web: TestClient) -> None:
    response = rpc(web, "server/discover")

    assert response.status_code == 200
    result = result_of(response)
    assert result["supportedVersions"] == ["2026-07-28"]
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == (
        "opentext-content-management"
    )


def test_tools_list_returns_all_tools_with_cache_hints_and_result_type(web: TestClient) -> None:
    result = result_of(rpc(web, "tools/list"))

    assert [tool["name"] for tool in result["tools"]] == list(TOOL_NAMES)
    assert result["resultType"] == "complete"
    assert result["ttlMs"] == 30000
    assert result["cacheScope"] == "private"


def test_tool_call_over_http_returns_structured_content_and_request_id(
    web: TestClient, harness: Harness
) -> None:
    harness.backend.script = [httpx.Response(200, json=payloads.single(payloads.node_props(2000)))]

    response = rpc(
        web, "tools/call", {"name": "get_node", "arguments": {"node_id": 2000}}, name="get_node"
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"]
    result = result_of(response)
    assert result["isError"] is False
    assert result["structuredContent"]["id"] == 2000


def test_tool_error_over_http_is_an_is_error_result_not_a_protocol_error(
    web: TestClient, harness: Harness
) -> None:
    harness.backend.script = [httpx.Response(404)]

    response = rpc(
        web, "tools/call", {"name": "get_node", "arguments": {"node_id": 9}}, name="get_node"
    )

    result = result_of(response)
    assert response.status_code == 200
    assert result["isError"] is True
    assert "not found" in result["content"][0]["text"]
    assert "otcs.example.com" not in response.text


# ----------------------------------------------------------------- observability


def test_traceparent_from_request_meta_is_forwarded_to_opentext(
    web: TestClient, harness: Harness
) -> None:
    rpc(
        web,
        "tools/call",
        {"name": "get_node", "arguments": {"node_id": 1}},
        name="get_node",
        meta={"traceparent": TRACEPARENT, "tracestate": "vendor=1"},
    )

    request = harness.backend.data_requests[0]
    assert request.headers["traceparent"] == TRACEPARENT
    assert request.headers["tracestate"] == "vendor=1"


@pytest.mark.parametrize(
    "bad", ["not-a-traceparent", "00-xyz-abc-01", TRACEPARENT + "\nInjected: 1", ""]
)
def test_invalid_traceparent_is_never_forwarded(
    web: TestClient, harness: Harness, bad: str
) -> None:
    rpc(
        web,
        "tools/call",
        {"name": "get_node", "arguments": {"node_id": 1}},
        name="get_node",
        meta={"traceparent": bad},
    )

    assert "traceparent" not in harness.backend.data_requests[0].headers


def test_trace_context_does_not_leak_between_requests(web: TestClient, harness: Harness) -> None:
    call = {"name": "get_node", "arguments": {"node_id": 1}}
    rpc(web, "tools/call", call, name="get_node", meta={"traceparent": TRACEPARENT})
    rpc(web, "tools/call", call, name="get_node")

    first, second = harness.backend.data_requests
    assert "traceparent" in first.headers
    assert "traceparent" not in second.headers


def test_metrics_endpoint_requires_authentication(web: TestClient) -> None:
    assert web.get("/metrics").status_code == 401


def test_metrics_endpoint_reports_tool_backend_and_http_counters(
    web: TestClient, harness: Harness
) -> None:
    harness.backend.script = [httpx.Response(200, json=payloads.single(payloads.node_props(5)))]
    rpc(web, "tools/call", {"name": "get_node", "arguments": {"node_id": 5}}, name="get_node")

    response = web.get("/metrics", auth=AUTH)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    text = response.text
    assert 'otcs_mcp_tool_calls_total{tool="get_node",outcome="ok"} 1' in text
    assert 'otcs_mcp_tool_duration_seconds_count{tool="get_node"} 1' in text
    assert 'otcs_mcp_backend_requests_total{method="GET",status="2xx"} 1' in text
    assert 'otcs_mcp_http_responses_total{status="2xx"}' in text
    assert AUTH[1] not in text


def test_metrics_endpoint_does_not_exist_when_disabled(http_settings: Settings) -> None:
    disabled = Harness(http_settings.model_copy(update={"mcp_metrics_enabled": False}))

    with TestClient(disabled.app, base_url="http://localhost") as client:
        assert client.get("/metrics", auth=AUTH).status_code == 404


def test_tool_call_logs_carry_the_correlation_id_of_the_http_request(
    web: TestClient, harness: Harness
) -> None:
    capture = _Capture()
    root = logging.getLogger()
    root.addHandler(capture)
    previous = root.level
    root.setLevel(logging.INFO)
    try:
        response = rpc(
            web, "tools/call", {"name": "get_node", "arguments": {"node_id": 1}}, name="get_node"
        )
    finally:
        root.removeHandler(capture)
        root.setLevel(previous)

    request_id = response.headers["x-request-id"]
    tool_rows = [cid for message, cid in capture.rows if message == "tool call"]
    assert tool_rows == [request_id]


# ----------------------------------------------------------------- write mode over HTTP


@pytest.fixture
def write_web(http_settings: Settings) -> Iterator[tuple[TestClient, Harness]]:
    harness = Harness(http_settings.model_copy(update={"mcp_read_only": False}))
    with TestClient(harness.app, base_url="http://localhost") as client:
        yield client, harness


def test_write_tools_are_listed_over_http_only_when_writes_are_enabled(
    web: TestClient, write_web: tuple[TestClient, Harness]
) -> None:
    read_only_names = [t["name"] for t in result_of(rpc(web, "tools/list"))["tools"]]
    write_names = [t["name"] for t in result_of(rpc(write_web[0], "tools/list"))["tools"]]

    assert read_only_names == list(TOOL_NAMES)
    assert write_names == list(ALL_TOOL_NAMES)


def test_overwrite_without_confirmation_is_refused_over_http_and_opentext_is_not_called(
    write_web: tuple[TestClient, Harness],
) -> None:
    client, harness = write_web
    arguments = {
        "node_id": 1,
        "category_id": 9830,
        "values": {"9830_2": "x"},
        "confirm": False,
    }

    response = rpc(
        client,
        "tools/call",
        {"name": "update_node_category", "arguments": arguments},
        name="update_node_category",
    )

    result = result_of(response)
    assert result["isError"] is True
    assert "explicitly approved" in result["content"][0]["text"]
    assert harness.backend.requests == []


def test_write_call_still_requires_basic_authentication_over_http(
    write_web: tuple[TestClient, Harness],
) -> None:
    client, harness = write_web

    response = rpc(
        client,
        "tools/call",
        {"name": "create_folder", "arguments": {"parent_id": 2000, "name": "x"}},
        name="create_folder",
        auth=None,
    )

    assert response.status_code == 401
    assert harness.backend.requests == []


def test_create_folder_works_over_http_with_credentials(
    write_web: tuple[TestClient, Harness],
) -> None:
    client, harness = write_web
    harness.backend.script = [httpx.Response(200, json=payloads.created(9001, name="Reports"))]

    response = rpc(
        client,
        "tools/call",
        {"name": "create_folder", "arguments": {"parent_id": 2000, "name": "Reports"}},
        name="create_folder",
    )

    result = result_of(response)
    assert result["isError"] is False
    assert result["structuredContent"]["id"] == 9001
