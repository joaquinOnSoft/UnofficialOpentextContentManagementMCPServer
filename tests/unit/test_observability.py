# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from otcs_mcp import observability
from otcs_mcp.config import Settings
from otcs_mcp.observability import Metrics, trace_headers_from_meta
from otcs_mcp.server import build_server
from tests import payloads
from tests.conftest import Backend, ClientFactory

TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


# ----------------------------------------------------------------- trace context


def test_valid_traceparent_and_tracestate_are_extracted() -> None:
    meta = {"traceparent": TRACEPARENT, "tracestate": "vendor=abc,other=1"}

    assert trace_headers_from_meta(meta) == meta


def test_tracestate_is_ignored_without_a_valid_traceparent() -> None:
    assert trace_headers_from_meta({"tracestate": "k=v"}) == {}


@pytest.mark.parametrize(
    "meta",
    [
        None,
        {},
        {"traceparent": 5},
        {"traceparent": "garbage"},
        {"traceparent": TRACEPARENT.upper()},
        {"traceparent": TRACEPARENT + "-extra"},
        {"traceparent": "00-" + "0" * 32 + "-" + "0" * 15 + "-01"},
        {"traceparent": TRACEPARENT + "\r\nX: y"},
    ],
)
def test_invalid_traceparent_is_dropped(meta: dict[str, Any] | None) -> None:
    assert trace_headers_from_meta(meta) == {}


@pytest.mark.parametrize("tracestate", [5, "x" * 513, "bad\nstate", "tab\tstate", ""])
def test_invalid_tracestate_is_dropped_but_traceparent_is_kept(tracestate: object) -> None:
    result = trace_headers_from_meta({"traceparent": TRACEPARENT, "tracestate": tracestate})

    assert result == {"traceparent": TRACEPARENT}


def test_tracestate_at_the_length_limit_is_kept() -> None:
    value = "k=" + "v" * 510

    assert (
        trace_headers_from_meta({"traceparent": TRACEPARENT, "tracestate": value})["tracestate"]
        == value
    )


# ----------------------------------------------------------------- metrics registry


def test_render_of_an_empty_registry_still_declares_every_metric() -> None:
    text = Metrics().render()

    for name in (
        "otcs_mcp_tool_calls_total",
        "otcs_mcp_tool_duration_seconds",
        "otcs_mcp_backend_requests_total",
        "otcs_mcp_backend_duration_seconds",
        "otcs_mcp_http_responses_total",
    ):
        assert f"# TYPE {name} " in text
    assert text.endswith("\n")


def test_tool_calls_are_counted_per_tool_and_outcome() -> None:
    metrics = Metrics()
    metrics.observe_tool("get_node", "ok", 0.01)
    metrics.observe_tool("get_node", "ok", 0.02)
    metrics.observe_tool("get_node", "error", 0.03)

    text = metrics.render()
    assert 'otcs_mcp_tool_calls_total{tool="get_node",outcome="ok"} 2' in text
    assert 'otcs_mcp_tool_calls_total{tool="get_node",outcome="error"} 1' in text


def test_latency_histogram_is_cumulative_with_sum_and_count() -> None:
    metrics = Metrics()
    for seconds in (0.04, 0.2, 3.0, 99.0):
        metrics.observe_tool("search_nodes", "ok", seconds)

    text = metrics.render()
    assert 'otcs_mcp_tool_duration_seconds_bucket{tool="search_nodes",le="0.05"} 1' in text
    assert 'otcs_mcp_tool_duration_seconds_bucket{tool="search_nodes",le="0.25"} 2' in text
    assert 'otcs_mcp_tool_duration_seconds_bucket{tool="search_nodes",le="5.0"} 3' in text
    assert 'otcs_mcp_tool_duration_seconds_bucket{tool="search_nodes",le="+Inf"} 4' in text
    assert 'otcs_mcp_tool_duration_seconds_count{tool="search_nodes"} 4' in text
    assert 'otcs_mcp_tool_duration_seconds_sum{tool="search_nodes"} 102.240000' in text


def test_backend_and_http_series_are_rendered_without_tool_labels() -> None:
    metrics = Metrics()
    metrics.observe_backend("GET", "2xx", 0.5)
    metrics.observe_http("4xx")

    text = metrics.render()
    assert 'otcs_mcp_backend_requests_total{method="GET",status="2xx"} 1' in text
    assert 'otcs_mcp_backend_duration_seconds_bucket{le="0.5"} 1' in text
    assert "otcs_mcp_backend_duration_seconds_count 1" in text
    assert 'otcs_mcp_http_responses_total{status="4xx"} 1' in text


def test_label_values_are_escaped_in_the_exposition_format() -> None:
    metrics = Metrics()
    metrics.observe_tool('we"ird\\name\nx', "ok", 0.1)

    text = metrics.render()
    assert 'tool="we\\"ird\\\\name\\nx"' in text
    assert "\nx" not in text.replace("\\nx", "")


# ----------------------------------------------------------------- middleware through the server


@pytest.fixture
def metrics() -> Metrics:
    return Metrics()


async def test_tool_calls_are_timed_and_counted_with_an_injected_clock(
    settings: Settings, make_client: ClientFactory, backend: Backend, metrics: Metrics
) -> None:
    ticks = iter([10.0, 10.25, 10.25])
    mcp = build_server(settings, client=make_client(), metrics=metrics)
    mcp.middleware[-1]._clock = lambda: next(ticks)  # type: ignore[attr-defined]
    backend.script = [httpx.Response(200, json=payloads.single(payloads.node_props(1)))]

    async with Client(mcp) as client:
        await client.call_tool("get_node", {"node_id": 1})

    text = metrics.render()
    assert 'otcs_mcp_tool_calls_total{tool="get_node",outcome="ok"} 1' in text
    assert 'otcs_mcp_tool_duration_seconds_sum{tool="get_node"} 0.250000' in text


async def test_failed_tool_calls_are_counted_as_errors(
    settings: Settings, make_client: ClientFactory, backend: Backend, metrics: Metrics
) -> None:
    backend.script = [httpx.Response(403)]
    async with Client(build_server(settings, client=make_client(), metrics=metrics)) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_node", {"node_id": 1})

    assert 'otcs_mcp_tool_calls_total{tool="get_node",outcome="error"} 1' in metrics.render()


async def test_unknown_tool_names_never_become_metric_labels(
    settings: Settings, make_client: ClientFactory, metrics: Metrics
) -> None:
    async with Client(build_server(settings, client=make_client(), metrics=metrics)) as client:
        for index in range(3):
            with pytest.raises(ToolError):
                await client.call_tool(f"attacker_tool_{index}", {})

    text = metrics.render()
    assert "attacker_tool" not in text
    assert 'otcs_mcp_tool_calls_total{tool="unknown",outcome="error"} 3' in text


async def test_trace_headers_reach_opentext_through_the_in_memory_client(
    settings: Settings, make_client: ClientFactory, backend: Backend
) -> None:
    backend.script = [httpx.Response(200, json=payloads.single(payloads.node_props(1)))]
    async with Client(build_server(settings, client=make_client())) as client:
        await client.call_tool(
            "get_node", {"node_id": 1}, meta={"traceparent": TRACEPARENT, "tracestate": "a=b"}
        )

    request = backend.data_requests[0]
    assert request.headers["traceparent"] == TRACEPARENT
    assert request.headers["tracestate"] == "a=b"
    assert observability.trace_headers_var.get() == {}  # restored after the call


async def test_trace_context_is_restored_even_when_the_tool_fails(
    settings: Settings, make_client: ClientFactory, backend: Backend
) -> None:
    backend.script = [httpx.Response(404)]
    async with Client(build_server(settings, client=make_client())) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_node", {"node_id": 1}, meta={"traceparent": TRACEPARENT})

    assert observability.trace_headers_var.get() == {}
