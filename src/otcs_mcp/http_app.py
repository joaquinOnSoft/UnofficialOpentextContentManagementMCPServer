# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Streamable HTTP transport: security middleware and ASGI app factory.

Every request is authorised on its own (the 2026-07-28 protocol has no sessions).
Checks run in this order, cheapest and least informative first:

1. ``/healthz``: unauthenticated liveness probe that returns nothing sensitive.
2. ``Host`` allowlist (DNS rebinding protection)  -> 421
3. ``Origin`` policy                               -> 403
4. Rate limit per client address (before auth)     -> 429
5. HTTP Basic authentication                       -> 401
6. Request body size limit                         -> 413
"""

import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from otcs_mcp.config import Settings
from otcs_mcp.observability import Metrics, correlation_id_var
from otcs_mcp.security import (
    SlidingWindowRateLimiter,
    is_host_allowed,
    is_origin_allowed,
    verify_basic_auth,
)

logger = logging.getLogger(__name__)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

HEALTH_PATH = "/healthz"
METRICS_PATH = "/metrics"
MCP_PATH = "/mcp"
REQUEST_ID_HEADER = "x-request-id"
BASIC_CHALLENGE = 'Basic realm="otcs-mcp", charset="UTF-8"'

_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_DISCONNECTED = object()


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return str(value.decode("latin-1"))
    return None


def _client_address(scope: Scope) -> str:
    client = scope.get("client")
    return str(client[0]) if client else "unknown"


class SecurityMiddleware:
    """Authentication, origin/host validation, limits, correlation IDs and access logs."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        limiter: SlidingWindowRateLimiter | None = None,
        metrics: Metrics | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._app = app
        self._settings = settings
        self._limiter = limiter or SlidingWindowRateLimiter(settings.mcp_rate_limit_per_minute)
        self._metrics = metrics
        self._clock = clock
        # Both are validated at startup, so reading them here is safe.
        self._user = settings.mcp_http_auth_user or ""
        password = settings.mcp_http_auth_password
        self._password = password.get_secret_value() if password else ""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        request_id = self._request_id(scope)
        token = correlation_id_var.set(request_id)
        started = self._clock()
        status = 0

        async def tracked_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = [
                    *message.get("headers", []),
                    (REQUEST_ID_HEADER.encode(), request_id.encode()),
                ]
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self._dispatch(scope, receive, tracked_send)
        finally:
            if self._metrics is not None and status:
                self._metrics.observe_http(f"{status // 100}xx")
            logger.info(
                "request",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status,
                    "client": _client_address(scope),
                    "seconds": round(self._clock() - started, 6),
                },
            )
            correlation_id_var.reset(token)

    @staticmethod
    def _request_id(scope: Scope) -> str:
        supplied = _header(scope, b"x-request-id")
        if supplied and _REQUEST_ID_RE.fullmatch(supplied):
            return supplied
        return uuid.uuid4().hex

    async def _dispatch(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["path"] == HEALTH_PATH:
            if scope["method"] != "GET":
                await _respond(send, 405, "Method not allowed.", {"allow": "GET"})
            else:
                await _respond(send, 200, "ok")
            return
        if not is_host_allowed(_header(scope, b"host"), self._settings.mcp_allowed_hosts):
            await _respond(send, 421, "Host not allowed. Add it to MCP_ALLOWED_HOSTS.")
            return
        if not is_origin_allowed(_header(scope, b"origin"), self._settings.mcp_allowed_origins):
            await _respond(send, 403, "Origin not allowed.")
            return
        if not self._limiter.allow(_client_address(scope)):
            await _respond(send, 429, "Too many requests.", {"retry-after": "60"})
            return
        if not verify_basic_auth(
            _header(scope, b"authorization"),
            expected_user=self._user,
            expected_password=self._password,
        ):
            logger.warning("authentication failed", extra={"client": _client_address(scope)})
            await _respond(
                send, 401, "Authentication required.", {"www-authenticate": BASIC_CHALLENGE}
            )
            return
        await self._forward_with_body_limit(scope, receive, send)

    async def _forward_with_body_limit(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = self._settings.mcp_max_body_bytes
        declared = _header(scope, b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _respond(send, 413, "Request body too large.")
            return
        body = await _read_body(receive, limit)
        if body is _DISCONNECTED:
            return  # the client went away: nobody is left to answer
        if body is None:
            await _respond(send, 413, "Request body too large.")
            return
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self._app(scope, replay, send)


async def _read_body(receive: Receive, limit: int) -> bytes | object | None:
    """Read the whole request body; ``None`` when it exceeds ``limit``."""
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return _DISCONNECTED
        chunk = bytes(message.get("body", b""))
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


async def _respond(
    send: Send, status: int, text: str, headers: dict[str, str] | None = None
) -> None:
    body = json.dumps({"status": status, "message": text}).encode() if status != 200 else b"ok"
    content_type = b"application/json" if status != 200 else b"text/plain; charset=utf-8"
    raw_headers = [
        (b"content-type", content_type),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
        (b"x-content-type-options", b"nosniff"),
    ]
    raw_headers += [(k.encode(), v.encode()) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": raw_headers})
    await send({"type": "http.response.body", "body": body})


def build_http_app(
    settings: Settings,
    mcp: FastMCP,
    *,
    metrics: Metrics | None = None,
    limiter: SlidingWindowRateLimiter | None = None,
) -> Starlette:
    """Create the ASGI application that serves the MCP endpoint at ``/mcp``."""
    if metrics is not None and settings.mcp_metrics_enabled:

        @mcp.custom_route(METRICS_PATH, methods=["GET"], include_in_schema=False)
        async def metrics_endpoint(_: Request) -> Response:
            return PlainTextResponse(
                metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
            )

    return mcp.http_app(
        path=MCP_PATH,
        stateless_http=True,
        middleware=[
            Middleware(
                SecurityMiddleware,
                settings=settings,
                limiter=limiter,
                metrics=metrics,
            )
        ],
    )
