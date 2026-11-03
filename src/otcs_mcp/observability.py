# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Correlation IDs, W3C trace-context propagation and basic Prometheus metrics."""

import logging
import re
import time
from collections.abc import Callable, Collection, Mapping
from contextvars import ContextVar
from types import MappingProxyType
from typing import Any

from fastmcp.server.dependencies import fastmcp_request_ctx
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

logger = logging.getLogger(__name__)

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_NO_HEADERS: Mapping[str, str] = MappingProxyType({})
trace_headers_var: ContextVar[Mapping[str, str]] = ContextVar("trace_headers", default=_NO_HEADERS)

_TRACEPARENT_RE = re.compile(r"[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}")
_TRACESTATE_MAX_LENGTH = 512
_TRACESTATE_RE = re.compile(r"[\x20-\x7e]+")
LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def trace_headers_from_meta(meta: Mapping[str, Any] | None) -> dict[str, str]:
    """Extract valid W3C ``traceparent``/``tracestate`` values from a request ``_meta``.

    Invalid values are dropped silently: they come from the client and must never
    be forwarded to the backend unchecked.
    """
    headers: dict[str, str] = {}
    if not meta:
        return headers
    traceparent = meta.get("traceparent")
    if isinstance(traceparent, str) and _TRACEPARENT_RE.fullmatch(traceparent):
        headers["traceparent"] = traceparent
        tracestate = meta.get("tracestate")
        if (
            isinstance(tracestate, str)
            and len(tracestate) <= _TRACESTATE_MAX_LENGTH
            and _TRACESTATE_RE.fullmatch(tracestate)
        ):
            headers["tracestate"] = tracestate
    return headers


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class _Histogram:
    def __init__(self) -> None:
        self.buckets = [0] * len(LATENCY_BUCKETS)
        self.count = 0
        self.total = 0.0

    def observe(self, seconds: float) -> None:
        self.count += 1
        self.total += seconds
        for index, bound in enumerate(LATENCY_BUCKETS):
            if seconds <= bound:
                self.buckets[index] += 1


class Metrics:
    """Small in-process metrics registry rendered in the Prometheus text format.

    Label values are always drawn from small fixed sets (tool names, HTTP methods,
    status classes) so cardinality stays bounded.
    """

    def __init__(self) -> None:
        self._tool_calls: dict[tuple[str, str], int] = {}
        self._tool_latency: dict[str, _Histogram] = {}
        self._backend_requests: dict[tuple[str, str], int] = {}
        self._backend_latency = _Histogram()
        self._http_requests: dict[str, int] = {}

    def observe_tool(self, tool: str, outcome: str, seconds: float) -> None:
        """Record one tool call."""
        key = (tool, outcome)
        self._tool_calls[key] = self._tool_calls.get(key, 0) + 1
        self._tool_latency.setdefault(tool, _Histogram()).observe(seconds)

    def observe_backend(self, method: str, status_class: str, seconds: float) -> None:
        """Record one request to OpenText (``status_class`` is e.g. ``2xx`` or ``error``)."""
        key = (method, status_class)
        self._backend_requests[key] = self._backend_requests.get(key, 0) + 1
        self._backend_latency.observe(seconds)

    def observe_http(self, status_class: str) -> None:
        """Record one HTTP response served to a client."""
        self._http_requests[status_class] = self._http_requests.get(status_class, 0) + 1

    def render(self) -> str:
        """Return all metrics in the Prometheus text exposition format."""
        lines = [
            "# HELP otcs_mcp_tool_calls_total Tool calls by tool and outcome.",
            "# TYPE otcs_mcp_tool_calls_total counter",
        ]
        for (tool, outcome), value in sorted(self._tool_calls.items()):
            labels = f'tool="{_escape_label(tool)}",outcome="{outcome}"'
            lines.append(f"otcs_mcp_tool_calls_total{{{labels}}} {value}")
        lines += [
            "# HELP otcs_mcp_tool_duration_seconds Tool call latency.",
            "# TYPE otcs_mcp_tool_duration_seconds histogram",
        ]
        for tool, histogram in sorted(self._tool_latency.items()):
            lines += self._histogram_lines(
                "otcs_mcp_tool_duration_seconds", f'tool="{_escape_label(tool)}"', histogram
            )
        lines += [
            "# HELP otcs_mcp_backend_requests_total Requests to OpenText by method and status.",
            "# TYPE otcs_mcp_backend_requests_total counter",
        ]
        for (method, status_class), value in sorted(self._backend_requests.items()):
            labels = f'method="{method}",status="{status_class}"'
            lines.append(f"otcs_mcp_backend_requests_total{{{labels}}} {value}")
        lines += [
            "# HELP otcs_mcp_backend_duration_seconds Latency of requests to OpenText.",
            "# TYPE otcs_mcp_backend_duration_seconds histogram",
            *self._histogram_lines("otcs_mcp_backend_duration_seconds", "", self._backend_latency),
            "# HELP otcs_mcp_http_responses_total HTTP responses served by status class.",
            "# TYPE otcs_mcp_http_responses_total counter",
        ]
        for status_class, value in sorted(self._http_requests.items()):
            lines.append(f'otcs_mcp_http_responses_total{{status="{status_class}"}} {value}')
        return "\n".join(lines) + "\n"

    @staticmethod
    def _histogram_lines(name: str, labels: str, histogram: _Histogram) -> list[str]:
        prefix = f"{labels}," if labels else ""
        lines = [
            f'{name}_bucket{{{prefix}le="{bound}"}} {count}'
            for bound, count in zip(LATENCY_BUCKETS, histogram.buckets, strict=True)
        ]
        lines.append(f'{name}_bucket{{{prefix}le="+Inf"}} {histogram.count}')
        suffix = f"{{{labels}}}" if labels else ""
        lines.append(f"{name}_sum{suffix} {histogram.total:.6f}")
        lines.append(f"{name}_count{suffix} {histogram.count}")
        return lines


class ObservabilityMiddleware(Middleware):
    """Time tool calls, record metrics and expose the caller's trace context to the client."""

    def __init__(
        self,
        metrics: Metrics,
        *,
        known_tools: Collection[str],
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._metrics = metrics
        self._known_tools = frozenset(known_tools)
        self._clock = clock

    async def on_call_tool(
        self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]
    ) -> Any:
        """Wrap each ``tools/call``."""
        requested = str(getattr(context.message, "name", ""))
        # Client-supplied names never become labels unless they are real tools.
        tool = requested if requested in self._known_tools else "unknown"
        request_context = fastmcp_request_ctx.get()
        meta = request_context.meta if request_context is not None else None
        token = trace_headers_var.set(trace_headers_from_meta(meta))
        started = self._clock()
        outcome = "ok"
        try:
            return await call_next(context)
        except Exception:
            outcome = "error"
            raise
        finally:
            elapsed = self._clock() - started
            trace_headers_var.reset(token)
            self._metrics.observe_tool(tool, outcome, elapsed)
            logger.info("tool call", extra={"tool": tool, "outcome": outcome, "seconds": elapsed})
