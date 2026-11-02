# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Shared fixtures. No test in this suite touches the network or a real OpenText server."""

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from otcs_mcp.client import OtcsClient
from otcs_mcp.config import Settings

BASE_URL = "https://otcs.example.com/cs/cs"
PASSWORD = "s3cret-pw"

_ENV_PREFIXES = ("OTCS_", "MCP_", "TLS_")


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make tests independent of the developer's shell and of any real .env file."""
    import os

    for name in list(os.environ):
        if name.startswith(_ENV_PREFIXES) or name == "LOG_LEVEL":
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        otcs_base_url=BASE_URL,
        otcs_username="svc",
        otcs_password=PASSWORD,
        otcs_timeout_seconds=5,
        otcs_max_retries=2,
        otcs_max_concurrency=4,
    )


@pytest.fixture
def sleeps() -> list[float]:
    """Recorded backoff delays (the fake sleep never actually waits)."""
    return []


class Backend:
    """Programmable fake OpenText server used through ``httpx.MockTransport``.

    * ``POST .../v1/auth`` issues ``ticket-1``, ``ticket-2``, ...
    * other requests consume ``script`` (responses or exceptions) and then default to 200.
    * tickets in ``expired`` get HTTP 401.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.auth_requests: list[httpx.Request] = []
        self.issued: list[str] = []
        self.auth_response: httpx.Response | None = None
        self.script: list[httpx.Response | Exception] = []
        self.expired: set[str] = set()
        self.max_in_flight = 0
        self._in_flight = 0

    @property
    def data_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r not in self.auth_requests]

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST" and request.url.path.endswith("/v1/auth"):
            self.auth_requests.append(request)
            if self.auth_response is not None:
                return self.auth_response
            ticket = f"ticket-{len(self.issued) + 1}"
            self.issued.append(ticket)
            return httpx.Response(200, json={"ticket": ticket})
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            await asyncio.sleep(0)
            if request.headers.get("OTCSTicket") in self.expired:
                return httpx.Response(401)
            if self.script:
                item = self.script.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            return httpx.Response(200, json={"ok": True, "path": request.url.path})
        finally:
            self._in_flight -= 1


@pytest.fixture
def backend() -> Backend:
    return Backend()


ClientFactory = Callable[..., OtcsClient]


@pytest.fixture
async def make_client(
    backend: Backend, settings: Settings, sleeps: list[float]
) -> AsyncIterator[ClientFactory]:
    """Build clients wired to the fake backend; they are closed after the test."""
    created: list[OtcsClient] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    def factory(**overrides: Any) -> OtcsClient:
        effective = settings.model_copy(update=overrides) if overrides else settings
        client = OtcsClient(effective, transport=httpx.MockTransport(backend), sleep=fake_sleep)
        created.append(client)
        return client

    yield factory
    for client in created:
        await client.aclose()


@pytest.fixture
def client(make_client: ClientFactory) -> OtcsClient:
    return make_client()
