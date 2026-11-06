# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

import asyncio
import logging
from urllib.parse import parse_qs

import httpx
import pytest

from otcs_mcp import client as client_module
from otcs_mcp.client import OtcsClient
from otcs_mcp.config import Settings
from otcs_mcp.errors import (
    InvalidInputError,
    OtcsAuthError,
    OtcsBadRequestError,
    OtcsConflictError,
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
from tests.conftest import PASSWORD, Backend, ClientFactory

NODE_PATH = "/v2/nodes/2000"


# ----------------------------------------------------------------- authentication


async def test_client_authenticates_with_form_credentials_and_sends_ticket_header(
    client: OtcsClient, backend: Backend
) -> None:
    result = await client.get_json(NODE_PATH)

    assert result == {"ok": True, "path": "/cs/cs/api/v2/nodes/2000"}
    auth = backend.auth_requests[0]
    assert auth.url.path == "/cs/cs/api/v1/auth"
    assert auth.headers["content-type"] == "application/x-www-form-urlencoded"
    assert parse_qs(auth.content.decode()) == {"username": ["svc"], "password": [PASSWORD]}
    assert backend.data_requests[0].headers["OTCSTicket"] == "ticket-1"


async def test_client_reuses_ticket_across_requests_when_it_is_still_valid(
    client: OtcsClient, backend: Backend
) -> None:
    for _ in range(3):
        await client.get_json(NODE_PATH)

    assert len(backend.auth_requests) == 1
    assert len(backend.data_requests) == 3


async def test_concurrent_first_requests_share_a_single_authentication(
    client: OtcsClient, backend: Backend
) -> None:
    await asyncio.gather(*(client.get_json(NODE_PATH) for _ in range(6)))

    assert len(backend.auth_requests) == 1


async def test_auth_failure_raises_auth_error_when_credentials_are_rejected(
    client: OtcsClient, backend: Backend
) -> None:
    backend.auth_response = httpx.Response(401, json={"error": "Invalid username/password"})

    with pytest.raises(OtcsAuthError) as info:
        await client.get_json(NODE_PATH)

    assert info.value.status_code == 401
    assert "OTCS_USERNAME" in str(info.value)
    assert backend.data_requests == []


@pytest.mark.parametrize(
    "auth_response",
    [
        httpx.Response(200, json={}),
        httpx.Response(200, json={"ticket": ""}),
        httpx.Response(200, json={"ticket": 5}),
        httpx.Response(200, json=["ticket"]),
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, content=b"\xff\xfe"),
    ],
)
async def test_auth_raises_response_error_when_reply_has_no_usable_ticket(
    client: OtcsClient, backend: Backend, auth_response: httpx.Response
) -> None:
    backend.auth_response = auth_response

    with pytest.raises(OtcsResponseError):
        await client.get_json(NODE_PATH)


async def test_auth_failure_maps_http_400_when_request_is_invalid(
    make_client: ClientFactory, backend: Backend
) -> None:
    backend.auth_response = httpx.Response(400)

    with pytest.raises(OtcsBadRequestError):
        await make_client().get_json(NODE_PATH)


async def test_auth_is_retried_when_server_is_temporarily_unavailable(
    client: OtcsClient, backend: Backend, sleeps: list[float]
) -> None:
    responses = [httpx.Response(503), httpx.Response(200, json={"ticket": "after-retry"})]
    original = backend.__call__

    async def flaky(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and responses:
            backend.requests.append(request)
            backend.auth_requests.append(request)
            return responses.pop(0)
        return await original(request)

    transport = httpx.MockTransport(flaky)
    flaky_client = OtcsClient(client._settings, transport=transport, sleep=_recording_sleep(sleeps))
    try:
        await flaky_client.get_json(NODE_PATH)
    finally:
        await flaky_client.aclose()

    assert sleeps == [0.5]
    assert backend.data_requests[0].headers["OTCSTicket"] == "after-retry"


def _recording_sleep(sleeps: list[float]):  # type: ignore[no-untyped-def]
    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    return fake_sleep


# ----------------------------------------------------------------- ticket renewal


async def test_ticket_is_renewed_once_when_opentext_answers_401(
    client: OtcsClient, backend: Backend
) -> None:
    backend.expired = {"ticket-1"}

    result = await client.get_json(NODE_PATH)

    assert result["ok"] is True
    assert len(backend.auth_requests) == 2
    assert [r.headers["OTCSTicket"] for r in backend.data_requests] == ["ticket-1", "ticket-2"]


async def test_auth_error_is_raised_when_401_persists_after_a_single_renewal(
    client: OtcsClient, backend: Backend
) -> None:
    backend.script = [httpx.Response(401), httpx.Response(401), httpx.Response(401)]

    with pytest.raises(OtcsAuthError) as info:
        await client.get_json(NODE_PATH)

    assert info.value.status_code == 401
    assert len(backend.auth_requests) == 2
    assert len(backend.data_requests) == 2  # no infinite renewal loop


async def test_concurrent_requests_trigger_a_single_renewal_when_ticket_expires(
    client: OtcsClient, backend: Backend
) -> None:
    backend.expired = {"ticket-1"}

    results = await asyncio.gather(*(client.get_json(NODE_PATH) for _ in range(5)))

    assert all(result["ok"] for result in results)
    assert len(backend.auth_requests) == 2


async def test_renewal_is_skipped_when_another_request_already_replaced_the_ticket(
    client: OtcsClient, backend: Backend
) -> None:
    await client.get_json(NODE_PATH)  # obtains ticket-1
    client._ticket = "ticket-fresh"

    await client._renew_ticket(stale="ticket-1")

    assert client._ticket == "ticket-fresh"
    assert len(backend.auth_requests) == 1


async def test_renewal_authenticates_when_ticket_is_still_the_stale_one(
    client: OtcsClient, backend: Backend
) -> None:
    await client.get_json(NODE_PATH)

    await client._renew_ticket(stale="ticket-1")

    assert client._ticket == "ticket-2"


# ----------------------------------------------------------------- retries and backoff


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_get_is_retried_with_backoff_when_server_error_then_succeeds(
    client: OtcsClient, backend: Backend, sleeps: list[float], status: int
) -> None:
    backend.script = [httpx.Response(status), httpx.Response(200, json={"a": 1})]

    assert await client.get_json(NODE_PATH) == {"a": 1}
    assert sleeps == [0.5]


async def test_backoff_doubles_between_attempts_and_error_is_raised_when_retries_run_out(
    client: OtcsClient, backend: Backend, sleeps: list[float]
) -> None:
    backend.script = [httpx.Response(503)] * 3

    with pytest.raises(OtcsServerError) as info:
        await client.get_json(NODE_PATH)

    assert info.value.status_code == 503
    assert sleeps == [0.5, 1.0]
    assert len(backend.data_requests) == 3


async def test_get_is_retried_when_request_times_out_then_succeeds(
    client: OtcsClient, backend: Backend, sleeps: list[float]
) -> None:
    backend.script = [httpx.ReadTimeout("slow"), httpx.Response(200, json={"a": 1})]

    assert await client.get_json(NODE_PATH) == {"a": 1}
    assert sleeps == [0.5]


async def test_timeout_error_is_raised_when_every_attempt_times_out(
    client: OtcsClient, backend: Backend, sleeps: list[float]
) -> None:
    backend.script = [httpx.ReadTimeout("slow")] * 3

    with pytest.raises(OtcsTimeoutError) as info:
        await client.get_json(NODE_PATH)

    assert isinstance(info.value.__cause__, httpx.ReadTimeout)
    assert sleeps == [0.5, 1.0]


async def test_connection_error_is_raised_with_generic_message_when_server_unreachable(
    make_client: ClientFactory, backend: Backend
) -> None:
    client = make_client(otcs_max_retries=0)
    backend.script = [httpx.ConnectError("dns failure for otcs.example.com")]

    with pytest.raises(OtcsConnectionError) as info:
        await client.get_json(NODE_PATH)

    assert "otcs.example.com" not in str(info.value)


async def test_retry_after_header_is_honoured_when_server_rate_limits(
    client: OtcsClient, backend: Backend, sleeps: list[float]
) -> None:
    backend.script = [
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json={"a": 1}),
    ]

    await client.get_json(NODE_PATH)

    assert sleeps == [3.0]


@pytest.mark.parametrize(("header", "expected"), [("999", 30.0), ("abc", 0.5), ("-4", 0.0)])
async def test_retry_after_is_capped_or_ignored_when_value_is_extreme_or_invalid(
    client: OtcsClient, backend: Backend, sleeps: list[float], header: str, expected: float
) -> None:
    backend.script = [
        httpx.Response(429, headers={"Retry-After": header}),
        httpx.Response(200, json={}),
    ]

    await client.get_json(NODE_PATH)

    assert sleeps == [expected]


async def test_no_retries_happen_when_max_retries_is_zero(
    make_client: ClientFactory, backend: Backend, sleeps: list[float]
) -> None:
    backend.script = [httpx.Response(503)]

    with pytest.raises(OtcsServerError):
        await make_client(otcs_max_retries=0).get_json(NODE_PATH)

    assert sleeps == []
    assert len(backend.data_requests) == 1


async def test_non_idempotent_request_is_not_retried_on_server_error(
    client: OtcsClient, backend: Backend, sleeps: list[float]
) -> None:
    backend.script = [httpx.Response(503), httpx.Response(200)]

    response = await client._send_with_retries(
        "POST", "/v2/anything", max_bytes=1000, idempotent=False
    )

    assert response.status_code == 503
    assert sleeps == []
    assert len(backend.data_requests) == 1


async def test_non_idempotent_request_is_not_retried_on_timeout(
    client: OtcsClient, backend: Backend, sleeps: list[float]
) -> None:
    backend.script = [httpx.ReadTimeout("slow"), httpx.Response(200)]

    with pytest.raises(OtcsTimeoutError):
        await client._send_with_retries("POST", "/v2/anything", max_bytes=1000, idempotent=False)

    assert sleeps == []
    assert len(backend.data_requests) == 1


# ----------------------------------------------------------------- error mapping


@pytest.mark.parametrize(
    ("status", "error_class"),
    [
        (400, OtcsBadRequestError),
        (403, OtcsPermissionError),
        (404, OtcsNotFoundError),
        (409, OtcsConflictError),
        (429, OtcsRateLimitError),
        (500, OtcsServerError),
        (503, OtcsServerError),
        (302, OtcsResponseError),
        (418, OtcsError),
    ],
)
async def test_http_status_is_mapped_to_error_hierarchy_without_leaking_details(
    make_client: ClientFactory,
    backend: Backend,
    status: int,
    error_class: type[OtcsError],
) -> None:
    backend.script = [
        httpx.Response(
            status,
            headers={"Location": "https://internal.example.net/x"},
            json={"error": "stack trace at /opt/otcs/internal.oscript ticket-1"},
        )
    ]

    with pytest.raises(error_class) as info:
        await make_client(otcs_max_retries=0).get_json(NODE_PATH)

    assert type(info.value) is error_class
    assert info.value.status_code == status
    message = str(info.value)
    for secret in ("otcs.example.com", "internal.example.net", "ticket-", "oscript", PASSWORD):
        assert secret not in message


async def test_rate_limit_error_exposes_retry_after_when_header_is_present(
    make_client: ClientFactory, backend: Backend
) -> None:
    backend.script = [httpx.Response(429, headers={"Retry-After": "7"})]

    with pytest.raises(OtcsRateLimitError) as info:
        await make_client(otcs_max_retries=0).get_json(NODE_PATH)

    assert info.value.retry_after == 7.0


# ----------------------------------------------------------------- response handling


@pytest.mark.parametrize("body", [b"not json", b"{broken", b"\xff\xfe\xfd", b""])
async def test_malformed_json_raises_response_error(
    client: OtcsClient, backend: Backend, body: bytes
) -> None:
    backend.script = [httpx.Response(200, content=body)]

    with pytest.raises(OtcsResponseError, match="malformed"):
        await client.get_json(NODE_PATH)


async def test_oversized_json_raises_response_error_when_limit_is_exceeded(
    client: OtcsClient, backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client_module, "MAX_JSON_BYTES", 20)
    backend.script = [httpx.Response(200, json={"data": "x" * 200})]

    with pytest.raises(OtcsResponseError, match="maximum allowed size"):
        await client.get_json(NODE_PATH)


async def test_get_bytes_truncates_and_flags_when_body_exceeds_limit(
    client: OtcsClient, backend: Backend
) -> None:
    backend.script = [httpx.Response(200, content=b"x" * 100)]

    data, truncated = await client.get_bytes("/v2/nodes/2000/content", max_bytes=10)

    assert data == b"x" * 10
    assert truncated is True


async def test_get_bytes_returns_full_body_when_within_limit(
    client: OtcsClient, backend: Backend
) -> None:
    backend.script = [httpx.Response(200, content=b"hello")]

    data, truncated = await client.get_bytes("/v2/nodes/2000/content", max_bytes=10)

    assert (data, truncated) == (b"hello", False)
    assert backend.data_requests[0].headers["accept"] == "*/*"


# ----------------------------------------------------------------- request building


@pytest.mark.parametrize(
    "path",
    [
        "v2/nodes/1",
        "/v2/nodes/1?x=1",
        "/v2/nodes/1#frag",
        "/v2/../admin",
        "/v2//nodes",
        "/v2/nodes/a b",
        "/v2/%2e%2e/x",
        "",
        "/v2/nodes/1\n",
    ],
)
async def test_path_is_rejected_when_it_is_not_a_plain_api_path(
    client: OtcsClient, backend: Backend, path: str
) -> None:
    with pytest.raises(InvalidInputError):
        await client.get_json(path)

    assert backend.requests == []


async def test_params_are_cleaned_when_they_contain_none_booleans_or_lists(
    client: OtcsClient, backend: Backend
) -> None:
    await client.get_json(
        "/v2/nodes/2000/nodes",
        params={
            "limit": 5,
            "show_hidden": True,
            "flag": False,
            "skip": None,
            "where_type": [0, 144],
        },
    )

    query = backend.data_requests[0].url.params
    assert query["limit"] == "5"
    assert query["show_hidden"] == "true"
    assert query["flag"] == "false"
    assert "skip" not in query
    assert query.get_list("where_type") == ["0", "144"]


async def test_list_params_convert_booleans_when_values_are_booleans(
    client: OtcsClient, backend: Backend
) -> None:
    await client.get_json(NODE_PATH, params={"flags": [True, False]})

    assert backend.data_requests[0].url.params.get_list("flags") == ["true", "false"]


async def test_redirects_are_not_followed_when_server_replies_with_302(
    client: OtcsClient, backend: Backend
) -> None:
    backend.script = [httpx.Response(302, headers={"Location": "https://elsewhere.example/"})]

    with pytest.raises(OtcsResponseError, match="redirect"):
        await client.get_json(NODE_PATH)

    assert len(backend.data_requests) == 1


# ----------------------------------------------------------------- concurrency and lifecycle


async def test_parallel_requests_are_limited_when_max_concurrency_is_set(
    make_client: ClientFactory, backend: Backend
) -> None:
    limited = make_client(otcs_max_concurrency=2)

    await asyncio.gather(*(limited.get_json(NODE_PATH) for _ in range(8)))

    assert backend.max_in_flight <= 2
    assert len(backend.data_requests) == 8


async def test_client_can_be_closed_twice_and_works_as_context_manager(
    settings: Settings, backend: Backend
) -> None:
    async with OtcsClient(settings, transport=httpx.MockTransport(backend)) as managed:
        await managed.get_json(NODE_PATH)

    await managed.aclose()
    assert managed._http.is_closed


async def test_secrets_never_appear_in_logs_when_client_runs_at_debug_level(
    client: OtcsClient, backend: Backend, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    backend.expired = {"ticket-1"}

    await client.get_json(NODE_PATH)

    assert PASSWORD not in caplog.text
    assert "ticket-1" not in caplog.text
    assert "ticket-2" not in caplog.text
    assert "Obtained a new OpenText ticket" in caplog.text


async def test_ticket_acquired_while_waiting_for_the_lock_is_reused_without_authenticating(
    client: OtcsClient, backend: Backend
) -> None:
    async with client._ticket_lock:
        waiter = asyncio.create_task(client._current_ticket())
        await asyncio.sleep(0)
        client._ticket = "ticket-from-peer"

    assert await waiter == "ticket-from-peer"
    assert backend.auth_requests == []


async def test_post_form_json_sends_form_body_and_ticket_when_called(
    client: OtcsClient, backend: Backend
) -> None:
    backend.script = [httpx.Response(200, json={"results": []})]

    result = await client.post_form_json("/v2/search", data={"where": "invoice", "limit": "5"})

    assert result == {"results": []}
    request = backend.data_requests[0]
    assert request.method == "POST"
    assert request.headers["OTCSTicket"] == "ticket-1"
    assert parse_qs(request.content.decode()) == {"where": ["invoice"], "limit": ["5"]}


async def test_post_form_json_resends_form_body_when_ticket_is_renewed(
    client: OtcsClient, backend: Backend
) -> None:
    backend.expired = {"ticket-1"}

    await client.post_form_json("/v2/search", data={"where": "invoice"})

    bodies = [parse_qs(r.content.decode()) for r in backend.data_requests]
    assert bodies == [{"where": ["invoice"]}, {"where": ["invoice"]}]
    assert backend.data_requests[1].headers["OTCSTicket"] == "ticket-2"


async def test_post_form_json_is_retried_when_read_request_gets_server_error(
    client: OtcsClient, backend: Backend, sleeps: list[float]
) -> None:
    backend.script = [httpx.Response(503), httpx.Response(200, json={"ok": 1})]

    assert await client.post_form_json("/v2/search", data={"where": "x"}) == {"ok": 1}
    assert sleeps == [0.5]


async def test_trace_context_headers_are_forwarded_to_opentext_when_set(
    client: OtcsClient, backend: Backend
) -> None:
    token = trace_headers_var.set(
        {
            "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
            "tracestate": "k=v",
        }
    )
    try:
        await client.get_json(NODE_PATH)
    finally:
        trace_headers_var.reset(token)

    request = backend.data_requests[0]
    assert request.headers["traceparent"].startswith("00-0af7651916cd43dd8448eb211c80319c")
    assert request.headers["tracestate"] == "k=v"
    assert request.headers["OTCSTicket"] == "ticket-1"


async def test_no_trace_headers_are_sent_when_none_are_set(
    client: OtcsClient, backend: Backend
) -> None:
    await client.get_json(NODE_PATH)

    assert "traceparent" not in backend.data_requests[0].headers


async def test_backend_metrics_record_method_status_class_and_errors(
    settings: Settings, backend: Backend
) -> None:
    metrics = Metrics()
    backend.script = [httpx.Response(404), httpx.ReadTimeout("slow"), httpx.Response(200, json={})]
    async with OtcsClient(
        settings.model_copy(update={"otcs_max_retries": 0}),
        transport=httpx.MockTransport(backend),
        metrics=metrics,
    ) as metered:
        with pytest.raises(OtcsNotFoundError):
            await metered.get_json(NODE_PATH)
        with pytest.raises(OtcsTimeoutError):
            await metered.get_json(NODE_PATH)
        await metered.get_json(NODE_PATH)

    text = metrics.render()
    assert 'otcs_mcp_backend_requests_total{method="POST",status="2xx"} 1' in text
    assert 'otcs_mcp_backend_requests_total{method="GET",status="4xx"} 1' in text
    assert 'otcs_mcp_backend_requests_total{method="GET",status="error"} 1' in text
    assert 'otcs_mcp_backend_requests_total{method="GET",status="2xx"} 1' in text
    assert "ticket-1" not in text


# ----------------------------------------------------------------- write requests


async def test_write_json_sends_a_form_with_the_ticket_and_parses_the_reply(
    client: OtcsClient, backend: Backend
) -> None:
    backend.script = [httpx.Response(200, json={"results": {"id": 5}})]

    result = await client.write_json("POST", "/v2/nodes", data={"body": '{"type": 0}'})

    assert result == {"results": {"id": 5}}
    request = backend.data_requests[0]
    assert request.method == "POST"
    assert request.headers["OTCSTicket"] == "ticket-1"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert parse_qs(request.content.decode()) == {"body": ['{"type": 0}']}


async def test_write_json_uses_multipart_when_files_are_given(
    client: OtcsClient, backend: Backend
) -> None:
    backend.script = [httpx.Response(200, json={"ok": 1})]

    await client.write_json(
        "POST",
        "/v2/nodes",
        data={"body": '{"name": "a.txt"}'},
        files={"file": ("a.txt", b"hello world", "text/plain")},
    )

    request = backend.data_requests[0]
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    body = request.content
    assert b'name="body"' in body
    assert b'filename="a.txt"' in body
    assert b"Content-Type: text/plain" in body
    assert b"hello world" in body


async def test_write_json_supports_put(client: OtcsClient, backend: Backend) -> None:
    await client.write_json("PUT", "/v2/nodes/1/categories/2/", data={"2_3": "x"})

    assert backend.data_requests[0].method == "PUT"
    assert backend.data_requests[0].url.path.endswith("/v2/nodes/1/categories/2/")


@pytest.mark.parametrize("content", [b"", b"  \n"])
async def test_write_json_returns_an_empty_object_when_the_reply_body_is_empty(
    client: OtcsClient, backend: Backend, content: bytes
) -> None:
    backend.script = [httpx.Response(200, content=content)]

    assert await client.write_json("PUT", "/v2/nodes/1/categories/2/", data={}) == {}


@pytest.mark.parametrize("status", [500, 502, 503, 504, 429])
async def test_write_is_never_retried_after_a_server_error(
    client: OtcsClient, backend: Backend, sleeps: list[float], status: int
) -> None:
    backend.script = [httpx.Response(status), httpx.Response(200, json={})]

    with pytest.raises(OtcsError) as info:
        await client.write_json("POST", "/v2/nodes", data={"body": "{}"})

    assert info.value.status_code == status
    assert sleeps == []
    assert len(backend.data_requests) == 1


async def test_write_timeout_is_not_retried_and_warns_that_the_change_may_have_applied(
    client: OtcsClient, backend: Backend, sleeps: list[float]
) -> None:
    backend.script = [httpx.ReadTimeout("slow"), httpx.Response(200, json={})]

    with pytest.raises(OtcsTimeoutError, match="may or may not have been applied"):
        await client.write_json("POST", "/v2/nodes", data={"body": "{}"})

    assert sleeps == []
    assert len(backend.data_requests) == 1


async def test_write_connection_error_is_not_retried(
    client: OtcsClient, backend: Backend, sleeps: list[float]
) -> None:
    backend.script = [httpx.ConnectError("refused")]

    with pytest.raises(OtcsConnectionError):
        await client.write_json("POST", "/v2/nodes", data={"body": "{}"})

    assert sleeps == []


async def test_write_is_resent_once_with_the_same_content_after_a_401_renewal(
    client: OtcsClient, backend: Backend
) -> None:
    backend.expired = {"ticket-1"}

    await client.write_json(
        "POST",
        "/v2/nodes",
        data={"body": '{"name": "a"}'},
        files={"file": ("a.txt", b"payload", "text/plain")},
    )

    first, second = backend.data_requests
    assert (first.headers["OTCSTicket"], second.headers["OTCSTicket"]) == ("ticket-1", "ticket-2")
    assert b"payload" in first.content
    assert b"payload" in second.content
    assert len(backend.auth_requests) == 2


async def test_write_conflict_maps_to_a_conflict_error_with_guidance(
    client: OtcsClient, backend: Backend
) -> None:
    backend.script = [httpx.Response(409)]

    with pytest.raises(OtcsConflictError, match="metadata_token") as info:
        await client.write_json("PUT", "/v2/nodes/1/categories/2/", data={})

    assert info.value.status_code == 409


async def test_write_rejects_unsafe_paths_without_sending_anything(
    client: OtcsClient, backend: Backend
) -> None:
    with pytest.raises(InvalidInputError):
        await client.write_json("POST", "/v2/../admin", data={})

    assert backend.requests == []


async def test_oversized_write_reply_is_rejected(
    client: OtcsClient, backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client_module, "MAX_JSON_BYTES", 10)
    backend.script = [httpx.Response(200, content=b"x" * 100)]

    with pytest.raises(OtcsResponseError, match="maximum allowed size"):
        await client.write_json("POST", "/v2/nodes", data={})
