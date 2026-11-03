# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Asynchronous client for the OpenText Content Server REST API.

All network access of the project is concentrated here. Behaviour follows the
Content Server REST API 26.4 swagger: ``POST /v1/auth`` (form-encoded
``username``/``password``) returns ``{"ticket": ...}`` and the ticket is sent
in the ``OTCSTicket`` header of every other request.

Note: the swagger documents HTTP 400 (and 500 on some operations) with the text
"Could not get a node for {id}" when a node does not exist, so callers must not
rely on 404 alone to detect missing nodes.
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import httpx

from otcs_mcp.config import Settings
from otcs_mcp.errors import (
    InvalidInputError,
    OtcsAuthError,
    OtcsBadRequestError,
    OtcsConnectionError,
    OtcsError,
    OtcsNotFoundError,
    OtcsPermissionError,
    OtcsRateLimitError,
    OtcsResponseError,
    OtcsServerError,
    OtcsTimeoutError,
)
from otcs_mcp.observability import Metrics, trace_headers_var

logger = logging.getLogger(__name__)

TICKET_HEADER = "OTCSTicket"
AUTH_PATH = "/v1/auth"
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_AUTH_BYTES = 64 * 1024
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_CAP_SECONDS = 8.0
RETRY_AFTER_CAP_SECONDS = 30.0

_SAFE_PATH_RE = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@/-]*")

Sleep = Callable[[float], Awaitable[None]]
ParamValue = str | int | float | bool | None | Sequence[str | int | float | bool]


@dataclass(frozen=True, slots=True)
class _RawResponse:
    status_code: int
    headers: httpx.Headers
    body: bytes
    truncated: bool


def _check_path(path: str) -> str:
    """Defence in depth: reject anything that is not a plain API path."""
    if not _SAFE_PATH_RE.fullmatch(path) or "//" in path or ".." in path.split("/"):
        raise InvalidInputError("path", "must be a plain API path such as /v2/nodes/2000")
    return path


def _clean_params(params: Mapping[str, ParamValue] | None) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            cleaned[key] = "true" if value else "false"
        elif isinstance(value, Sequence) and not isinstance(value, str):
            cleaned[key] = [
                ("true" if item else "false") if isinstance(item, bool) else item for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return min(max(seconds, 0.0), RETRY_AFTER_CAP_SECONDS)


class OtcsClient:
    """Reusable async client: one HTTP session per process, ticket kept in memory only."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        metrics: Metrics | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._settings = settings
        self._metrics = metrics
        self._clock = clock
        self._max_retries = settings.otcs_max_retries
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=settings.otcs_api_url,
            timeout=httpx.Timeout(settings.otcs_timeout_seconds),
            verify=settings.otcs_verify_ssl,
            transport=transport,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=settings.otcs_max_concurrency,
                max_keepalive_connections=settings.otcs_max_concurrency,
            ),
            headers={"Accept": "application/json"},
        )
        self._semaphore = asyncio.Semaphore(settings.otcs_max_concurrency)
        self._ticket_lock = asyncio.Lock()
        self._ticket: str | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP session (safe to call more than once)."""
        await self._http.aclose()

    # ------------------------------------------------------------------ public API

    async def get_json(self, path: str, *, params: Mapping[str, ParamValue] | None = None) -> Any:
        """GET ``path`` (relative to ``<OTCS_BASE_URL>/api``) and return the parsed JSON."""
        response = await self._authorized_request(
            "GET", path, params=params, data=None, max_bytes=MAX_JSON_BYTES
        )
        return self._parse_json(response)

    async def post_form_json(self, path: str, *, data: Mapping[str, str]) -> Any:
        """POST a form to ``path`` and return the parsed JSON.

        Reserved for read-only operations that need a request body (the search
        endpoint). It is retried like a GET because it has no side effects.
        """
        response = await self._authorized_request(
            "POST", path, params=None, data=data, max_bytes=MAX_JSON_BYTES
        )
        return self._parse_json(response)

    @staticmethod
    def _parse_json(response: _RawResponse) -> Any:
        if response.truncated:
            raise OtcsResponseError("OpenText response exceeded the maximum allowed size")
        try:
            return json.loads(response.body.decode("utf-8"))
        except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError
            raise OtcsResponseError("OpenText returned a malformed JSON response") from exc

    async def get_bytes(
        self,
        path: str,
        *,
        max_bytes: int,
        params: Mapping[str, ParamValue] | None = None,
    ) -> tuple[bytes, bool]:
        """GET ``path`` reading at most ``max_bytes``; returns ``(data, truncated)``."""
        response = await self._authorized_request(
            "GET", path, params=params, data=None, max_bytes=max_bytes, accept="*/*"
        )
        return response.body, response.truncated

    # ------------------------------------------------------------------ ticket handling

    async def _current_ticket(self) -> str:
        ticket = self._ticket
        if ticket is not None:
            return ticket
        async with self._ticket_lock:
            ticket = self._ticket
            if ticket is None:
                ticket = await self._authenticate()
                self._ticket = ticket
            return ticket

    async def _renew_ticket(self, *, stale: str) -> None:
        """Replace ``stale`` once, even if many requests discover the expiry together."""
        async with self._ticket_lock:
            if self._ticket is not None and self._ticket != stale:
                return  # another request already renewed it
            self._ticket = await self._authenticate()

    async def _authenticate(self) -> str:
        response = await self._send_with_retries(
            "POST",
            AUTH_PATH,
            data={
                "username": self._settings.otcs_username,
                "password": self._settings.otcs_password.get_secret_value(),
            },
            max_bytes=MAX_AUTH_BYTES,
            idempotent=True,  # creating a ticket has no side effects worth protecting
        )
        if response.status_code == 401:
            raise OtcsAuthError(
                "OpenText rejected the configured service-account credentials (HTTP 401). "
                "Check OTCS_USERNAME and OTCS_PASSWORD.",
                status_code=401,
            )
        self._raise_for_status(response)
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except ValueError as exc:
            raise OtcsResponseError("OpenText authentication returned malformed JSON") from exc
        ticket = payload.get("ticket") if isinstance(payload, dict) else None
        if not isinstance(ticket, str) or not ticket:
            raise OtcsResponseError("OpenText authentication response did not contain a ticket")
        logger.info("Obtained a new OpenText ticket")
        return ticket

    # ------------------------------------------------------------------ request pipeline

    async def _authorized_request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, ParamValue] | None,
        data: Mapping[str, str] | None,
        max_bytes: int,
        accept: str | None = None,
    ) -> _RawResponse:
        _check_path(path)
        headers = {"Accept": accept} if accept else {}
        renewed = False
        while True:
            ticket = await self._current_ticket()
            response = await self._send_with_retries(
                method,
                path,
                params=_clean_params(params),
                data=data,
                headers={**headers, TICKET_HEADER: ticket},
                max_bytes=max_bytes,
                idempotent=True,  # only read operations are routed through this method
            )
            if response.status_code == 401 and not renewed:
                logger.info("OpenText rejected the ticket; renewing it once")
                await self._renew_ticket(stale=ticket)
                renewed = True
                continue
            return self._raise_for_status(response)

    async def _send_with_retries(
        self,
        method: str,
        path: str,
        *,
        max_bytes: int,
        idempotent: bool,
        params: dict[str, Any] | None = None,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> _RawResponse:
        attempt = 0
        while True:
            try:
                response = await self._send_once(
                    method, path, params=params, data=data, headers=headers, max_bytes=max_bytes
                )
            except (OtcsTimeoutError, OtcsConnectionError):
                if not idempotent or attempt >= self._max_retries:
                    raise
                await self._backoff(attempt, None)
                attempt += 1
                continue
            if (
                idempotent
                and response.status_code in RETRYABLE_STATUS
                and attempt < self._max_retries
            ):
                await self._backoff(attempt, response.headers.get("retry-after"))
                attempt += 1
                continue
            return response

    async def _send_once(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        data: Mapping[str, str] | None,
        headers: Mapping[str, str] | None,
        max_bytes: int,
    ) -> _RawResponse:
        request_headers = {**trace_headers_var.get(), **(headers or {})}
        started = self._clock()
        status_class = "error"
        try:
            async with (
                self._semaphore,
                self._http.stream(
                    method, path, params=params, data=data, headers=request_headers
                ) as response,
            ):
                body = bytearray()
                truncated = False
                async for chunk in response.aiter_bytes():
                    remaining = max_bytes - len(body)
                    if len(chunk) > remaining:
                        body.extend(chunk[:remaining])
                        truncated = True
                        break
                    body.extend(chunk)
                status_class = f"{response.status_code // 100}xx"
                return _RawResponse(response.status_code, response.headers, bytes(body), truncated)
        except httpx.TimeoutException as exc:
            raise OtcsTimeoutError(
                "The request to OpenText timed out. Retry later or raise OTCS_TIMEOUT_SECONDS."
            ) from exc
        except httpx.TransportError as exc:
            raise OtcsConnectionError(
                "Could not connect to the OpenText server. Check OTCS_BASE_URL and the network."
            ) from exc
        finally:
            if self._metrics is not None:
                self._metrics.observe_backend(method, status_class, self._clock() - started)

    async def _backoff(self, attempt: int, retry_after: str | None) -> None:
        delay = min(BACKOFF_BASE_SECONDS * 2**attempt, BACKOFF_CAP_SECONDS)
        server_delay = _retry_after_seconds(retry_after)
        if server_delay is not None:
            delay = server_delay
        logger.warning("Retrying OpenText request (attempt %d) in %.1fs", attempt + 1, delay)
        await self._sleep(delay)

    @staticmethod
    def _raise_for_status(response: _RawResponse) -> _RawResponse:
        status = response.status_code
        if 200 <= status < 300:
            return response
        error: OtcsError
        if 300 <= status < 400:
            error = OtcsResponseError(
                "OpenText returned an unexpected redirect. Check OTCS_BASE_URL.",
                status_code=status,
            )
        elif status == 400:
            error = OtcsBadRequestError(
                "OpenText rejected the request as invalid, or the node could not be "
                "processed (HTTP 400). Check the arguments.",
                status_code=status,
            )
        elif status == 401:
            error = OtcsAuthError(
                "OpenText authentication failed (HTTP 401). The service account may be "
                "invalid, locked or expired.",
                status_code=status,
            )
        elif status == 403:
            error = OtcsPermissionError(
                "The OpenText service account lacks permission for this operation (HTTP 403).",
                status_code=status,
            )
        elif status == 404:
            error = OtcsNotFoundError(
                "The requested OpenText resource was not found (HTTP 404).", status_code=status
            )
        elif status == 429:
            error = OtcsRateLimitError(
                "OpenText is rate limiting requests (HTTP 429). Retry later.",
                status_code=status,
                retry_after=_retry_after_seconds(response.headers.get("retry-after")),
            )
        elif status >= 500:
            error = OtcsServerError(
                f"OpenText reported an internal error (HTTP {status}). Retry later.",
                status_code=status,
            )
        else:
            error = OtcsError(
                f"OpenText returned an unexpected status (HTTP {status}).", status_code=status
            )
        raise error
