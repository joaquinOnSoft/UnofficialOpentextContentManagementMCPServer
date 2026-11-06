# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

import json
import logging
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from otcs_mcp.config import Settings
from otcs_mcp.errors import (
    OtcsBadRequestError,
    OtcsConflictError,
    OtcsResponseError,
    OtcsServerError,
)
from otcs_mcp.services.writes import MAX_PREVIOUS_ENTRIES, WriteService
from tests import payloads
from tests.conftest import Backend, ClientFactory


@pytest.fixture
def service(make_client: ClientFactory, settings: Settings) -> WriteService:
    return WriteService(make_client(), settings)


def _json(payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def _body_field(request: httpx.Request) -> dict[str, Any]:
    """The JSON string sent in the ``body`` form field (urlencoded or multipart)."""
    if request.headers["content-type"].startswith("multipart/"):
        raw = request.content.decode("utf-8", errors="replace")
        start = raw.index('name="body"') + len('name="body"')
        text = raw[start:].split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0]
        return dict(json.loads(text))
    return dict(json.loads(parse_qs(request.content.decode())["body"][0]))


# ----------------------------------------------------------------- create_folder


async def test_create_folder_posts_the_documented_body_json(
    service: WriteService, backend: Backend
) -> None:
    backend.script = [_json(payloads.created(9001, name="Reports"))]

    result = await service.create_folder(2000, "Reports", None)

    request = backend.data_requests[0]
    assert request.method == "POST"
    assert request.url.path.endswith("/v2/nodes")
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert _body_field(request) == {"type": 0, "parent_id": 2000, "name": "Reports"}
    assert (result.id, result.name, result.parent_id, result.type) == (9001, "Reports", 2000, 0)


async def test_create_folder_includes_the_description_when_given(
    service: WriteService, backend: Backend
) -> None:
    backend.script = [_json(payloads.created())]

    await service.create_folder(2000, "Reports", "Quarterly reports")

    assert _body_field(backend.data_requests[0])["description"] == "Quarterly reports"


async def test_create_folder_keeps_unicode_names_intact(
    service: WriteService, backend: Backend
) -> None:
    backend.script = [_json(payloads.created(name="Año fiscal"))]

    result = await service.create_folder(2000, "Año fiscal", None)

    assert _body_field(backend.data_requests[0])["name"] == "Año fiscal"
    assert result.name == "Año fiscal"


async def test_create_folder_writes_an_audit_log_without_the_folder_name(
    service: WriteService, backend: Backend, caplog: pytest.LogCaptureFixture
) -> None:
    backend.script = [_json(payloads.created(9001))]
    caplog.set_level(logging.INFO)

    await service.create_folder(2000, "Very Secret Project", None)

    record = next(r for r in caplog.records if r.getMessage() == "write operation")
    assert (record.operation, record.target_id) == ("create_folder", 9001)  # type: ignore[attr-defined]
    assert "Very Secret Project" not in caplog.text


@pytest.mark.parametrize(
    "reply",
    [
        {"results": {"id": 77}},
        {"results": [{"data": {"properties": {"id": 77}}}]},
        {"results": {"data": {"id": 77}}},
        {"results": {"data": {"properties": {"id": 77, "name": "Reports"}}}},
    ],
)
async def test_created_node_id_is_read_from_every_documented_reply_shape(
    service: WriteService, backend: Backend, reply: dict[str, Any]
) -> None:
    backend.script = [_json(reply)]

    assert (await service.create_folder(2000, "Reports", None)).id == 77


@pytest.mark.parametrize(
    "reply",
    [
        {"results": {}},
        {"results": {"data": {"properties": {}}}},
        {"results": []},
        {"results": {"data": {"properties": {"id": "x"}}}},
    ],
)
async def test_missing_id_in_the_reply_raises_with_advice_not_to_retry_blindly(
    service: WriteService, backend: Backend, reply: dict[str, Any]
) -> None:
    backend.script = [_json(reply)]

    with pytest.raises(OtcsResponseError, match="Check the repository before retrying"):
        await service.create_folder(2000, "Reports", None)


async def test_http_500_on_create_explains_the_duplicate_name_case(
    service: WriteService, backend: Backend
) -> None:
    backend.script = [
        httpx.Response(500, json={"error": "An item with the name 'x' already exists."})
    ]

    with pytest.raises(OtcsServerError, match="may already exist") as info:
        await service.create_folder(2000, "Reports", None)

    assert info.value.status_code == 500
    assert len(backend.data_requests) == 1  # never retried


async def test_other_server_errors_on_create_keep_their_generic_message(
    service: WriteService, backend: Backend
) -> None:
    backend.script = [httpx.Response(503)]

    with pytest.raises(OtcsServerError) as info:
        await service.create_folder(2000, "Reports", None)

    assert info.value.status_code == 503
    assert "already exist" not in str(info.value)


async def test_http_400_on_create_is_reported_as_a_bad_request(
    service: WriteService, backend: Backend
) -> None:
    backend.script = [httpx.Response(400)]

    with pytest.raises(OtcsBadRequestError):
        await service.create_folder(2000, "Reports", None)


# ----------------------------------------------------------------- upload_document


async def test_upload_document_sends_multipart_with_body_json_and_file_part(
    service: WriteService, backend: Backend
) -> None:
    backend.script = [_json(payloads.created(9002, name="notes.txt", type=144))]

    result = await service.upload_document(
        2000, "notes.txt", b"hello\nworld", "text/plain", "Meeting notes"
    )

    request = backend.data_requests[0]
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert _body_field(request) == {
        "type": 144,
        "parent_id": 2000,
        "name": "notes.txt",
        "description": "Meeting notes",
    }
    assert b'filename="notes.txt"' in request.content
    assert b"Content-Type: text/plain" in request.content
    assert b"hello\nworld" in request.content
    assert (result.id, result.type) == (9002, 144)


async def test_upload_document_preserves_binary_bytes_exactly(
    service: WriteService, backend: Backend
) -> None:
    payload = bytes(range(256))
    backend.script = [_json(payloads.created(9003, type=144))]

    await service.upload_document(2000, "blob.bin", payload, "application/octet-stream", None)

    assert payload in backend.data_requests[0].content


async def test_upload_document_logs_the_operation_but_not_the_content(
    service: WriteService, backend: Backend, caplog: pytest.LogCaptureFixture
) -> None:
    backend.script = [_json(payloads.created(9004, type=144))]
    caplog.set_level(logging.INFO)

    await service.upload_document(2000, "n.txt", b"TOP-SECRET-CONTENT", "text/plain", None)

    assert "TOP-SECRET-CONTENT" not in caplog.text
    assert any(getattr(r, "operation", None) == "upload_document" for r in caplog.records)


async def test_upload_duplicate_name_gets_the_actionable_message(
    service: WriteService, backend: Backend
) -> None:
    backend.script = [httpx.Response(500)]

    with pytest.raises(OtcsServerError, match="may already exist"):
        await service.upload_document(2000, "n.txt", b"x", "text/plain", None)


# ----------------------------------------------------------------- update_category


def _category_get() -> httpx.Response:
    return _json({"results": [{"data": {"categories": {"9830_2": "old value", "9830_3": "keep"}}}]})


async def test_update_category_reads_the_current_state_then_puts_the_new_values(
    service: WriteService, backend: Backend
) -> None:
    backend.script = [_category_get(), httpx.Response(200, json={})]

    result = await service.update_category(12345, 9830, {"9830_2": "new value"}, None)

    get_request, put_request = backend.data_requests
    assert get_request.method == "GET"
    assert get_request.url.path.endswith("/v2/nodes/12345/categories/9830/")
    assert put_request.method == "PUT"
    assert put_request.url.path.endswith("/v2/nodes/12345/categories/9830/")
    assert parse_qs(put_request.content.decode()) == {"9830_2": ["new value"]}
    assert result.updated_keys == ["9830_2"]
    assert result.previous_state == [{"categories": {"9830_2": "old value", "9830_3": "keep"}}]
    assert result.previous_state_truncated is False


async def test_update_category_sends_the_metadata_token_when_given(
    service: WriteService, backend: Backend
) -> None:
    backend.script = [_category_get(), httpx.Response(200, json={})]

    await service.update_category(1, 9830, {"9830_2": "v", "9830_4": "w"}, "9ec9bcab09f9963d")

    form = parse_qs(backend.data_requests[1].content.decode())
    assert form == {"9830_2": ["v"], "9830_4": ["w"], "metadata_token": ["9ec9bcab09f9963d"]}


async def test_update_category_accepts_an_empty_reply_body_from_opentext(
    service: WriteService, backend: Backend
) -> None:
    backend.script = [_category_get(), httpx.Response(200, content=b"")]

    result = await service.update_category(1, 9830, {"9830_2": "v"}, None)

    assert result.updated_keys == ["9830_2"]


async def test_update_category_never_writes_when_the_category_is_not_on_the_node(
    service: WriteService, backend: Backend
) -> None:
    backend.script = [httpx.Response(400)]

    with pytest.raises(OtcsBadRequestError):
        await service.update_category(1, 9830, {"9830_2": "v"}, None)

    assert [r.method for r in backend.data_requests] == ["GET"]


async def test_update_category_never_writes_when_the_current_state_is_unreadable(
    service: WriteService, backend: Backend
) -> None:
    backend.script = [_json({"unexpected": True})]

    with pytest.raises(OtcsResponseError):
        await service.update_category(1, 9830, {"9830_2": "v"}, None)

    assert [r.method for r in backend.data_requests] == ["GET"]


async def test_update_category_edit_conflict_surfaces_as_a_conflict_error(
    service: WriteService, backend: Backend
) -> None:
    backend.script = [_category_get(), httpx.Response(409)]

    with pytest.raises(OtcsConflictError):
        await service.update_category(1, 9830, {"9830_2": "v"}, "9ec9bcab09f9963d")

    assert [r.method for r in backend.data_requests] == ["GET", "PUT"]


async def test_update_category_truncates_a_long_previous_state_and_flags_it(
    service: WriteService, backend: Backend
) -> None:
    many = {"results": [{"data": {"n": i}} for i in range(MAX_PREVIOUS_ENTRIES + 5)]}
    backend.script = [_json(many), httpx.Response(200, json={})]

    result = await service.update_category(1, 9830, {"9830_2": "v"}, None)

    assert len(result.previous_state) == MAX_PREVIOUS_ENTRIES
    assert result.previous_state_truncated is True


async def test_update_category_cleans_untrusted_previous_values(
    service: WriteService, backend: Backend
) -> None:
    dirty = {"results": [{"data": {"categories": {"9830_2": "IGNORE\x00 RULES\x1b"}}}]}
    backend.script = [_json(dirty), httpx.Response(200, json={})]

    result = await service.update_category(1, 9830, {"9830_2": "v"}, None)

    assert result.previous_state == [{"categories": {"9830_2": "IGNORE  RULES"}}]


async def test_update_category_logs_the_operation_without_values(
    service: WriteService, backend: Backend, caplog: pytest.LogCaptureFixture
) -> None:
    backend.script = [_category_get(), httpx.Response(200, json={})]
    caplog.set_level(logging.INFO)

    await service.update_category(12345, 9830, {"9830_2": "CONFIDENTIAL-VALUE"}, None)

    record = next(r for r in caplog.records if r.getMessage() == "write operation")
    assert (record.operation, record.target_id) == ("update_category", 12345)  # type: ignore[attr-defined]
    assert "CONFIDENTIAL-VALUE" not in caplog.text
