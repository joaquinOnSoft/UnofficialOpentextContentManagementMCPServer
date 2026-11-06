# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Write tools: gating, confirmation, validation and error handling."""

import base64
import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from otcs_mcp.config import Settings
from otcs_mcp.errors import ConfigError
from otcs_mcp.observability import Metrics
from otcs_mcp.server import ALL_TOOL_NAMES, READ_TOOL_NAMES, WRITE_TOOL_NAMES, build_server
from otcs_mcp.services.writes import WriteService
from otcs_mcp.tools.writes import register_write_tools
from tests import payloads
from tests.conftest import Backend, ClientFactory

MAX_UPLOAD = 2000


@pytest.fixture
def write_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"mcp_read_only": False, "mcp_max_upload_bytes": MAX_UPLOAD})


@pytest.fixture
def metrics() -> Metrics:
    return Metrics()


@pytest.fixture
def write_server(write_settings: Settings, make_client: ClientFactory, metrics: Metrics) -> FastMCP:
    return build_server(write_settings, client=make_client(), metrics=metrics)


@pytest.fixture
async def writer(write_server: FastMCP) -> AsyncIterator[Client[Any]]:
    async with Client(write_server) as client:
        yield client


def _json(payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


async def _call(client: Client[Any], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(name, arguments)
    assert result.structured_content is not None
    return dict(result.structured_content)


def _category_get() -> httpx.Response:
    return _json({"results": [{"data": {"categories": {"9830_2": "old"}}}]})


# ----------------------------------------------------------------- gating


async def test_write_tools_do_not_exist_in_the_default_read_only_mode(
    settings: Settings, make_client: ClientFactory
) -> None:
    assert settings.mcp_read_only is True

    async with Client(build_server(settings, client=make_client())) as client:
        names = [tool.name for tool in await client.list_tools()]

    assert names == list(READ_TOOL_NAMES)
    assert not set(names) & set(WRITE_TOOL_NAMES)


async def test_calling_a_write_tool_in_read_only_mode_fails_and_never_reaches_opentext(
    settings: Settings, make_client: ClientFactory, backend: Backend
) -> None:
    async with Client(build_server(settings, client=make_client())) as client:
        for tool, arguments in (
            ("create_folder", {"parent_id": 2000, "name": "x"}),
            ("upload_document", {"parent_id": 2000, "name": "x.txt", "content_text": "x"}),
            (
                "update_node_category",
                {"node_id": 1, "category_id": 2, "values": {"2_2": "x"}, "confirm": True},
            ),
        ):
            with pytest.raises(ToolError):
                await client.call_tool(tool, arguments)

    assert backend.requests == []


def test_registering_write_tools_in_read_only_mode_is_refused_outright(
    settings: Settings, make_client: ClientFactory
) -> None:
    mcp = FastMCP("probe")

    with pytest.raises(ConfigError, match="MCP_READ_ONLY=true"):
        register_write_tools(mcp, WriteService(make_client(), settings), settings)


async def test_write_mode_registers_read_tools_first_then_exactly_the_three_write_tools(
    writer: Client[Any],
) -> None:
    names = [tool.name for tool in await writer.list_tools()]

    assert names == list(ALL_TOOL_NAMES)
    assert names[-3:] == ["create_folder", "upload_document", "update_node_category"]


async def test_write_mode_adds_a_warning_to_the_server_instructions(
    write_server: FastMCP, settings: Settings, make_client: ClientFactory
) -> None:
    read_only = build_server(settings, client=make_client())

    assert "Write tools are enabled" in (write_server.instructions or "")
    assert "confirm=true" in (write_server.instructions or "")
    assert "Write tools" not in (read_only.instructions or "")


# ----------------------------------------------------------------- annotations and schemas


async def test_write_tools_are_annotated_as_non_read_only_and_only_the_overwrite_is_destructive(
    writer: Client[Any],
) -> None:
    tools = {tool.name: tool for tool in await writer.list_tools()}

    for name in ("create_folder", "upload_document"):
        hints = tools[name].annotations
        assert hints is not None
        assert (hints.read_only_hint, hints.destructive_hint, hints.idempotent_hint) == (
            False,
            False,
            False,
        )
    overwrite = tools["update_node_category"].annotations
    assert overwrite is not None
    assert (overwrite.read_only_hint, overwrite.destructive_hint, overwrite.idempotent_hint) == (
        False,
        True,
        True,
    )
    for name in WRITE_TOOL_NAMES:
        assert tools[name].output_schema is not None
        assert tools[name].annotations.open_world_hint is False  # type: ignore[union-attr]


async def test_confirm_is_a_required_strict_boolean_in_the_overwrite_schema(
    writer: Client[Any],
) -> None:
    tool = next(t for t in await writer.list_tools() if t.name == "update_node_category")
    schema = tool.input_schema

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"node_id", "category_id", "values", "confirm"}
    assert schema["properties"]["confirm"]["type"] == "boolean"
    assert "OVERWRITES" in (tool.description or "")
    assert "confirm=true" in (tool.description or "")


async def test_upload_schema_bounds_content_by_the_configured_upload_limit(
    writer: Client[Any],
) -> None:
    tool = next(t for t in await writer.list_tools() if t.name == "upload_document")
    properties = tool.input_schema["properties"]

    assert properties["content_text"]["anyOf"][0]["maxLength"] == MAX_UPLOAD
    assert properties["content_base64"]["anyOf"][0]["maxLength"] > MAX_UPLOAD
    assert str(MAX_UPLOAD) in (tool.description or "")
    assert set(tool.input_schema["required"]) == {"parent_id", "name"}


# ----------------------------------------------------------------- create_folder


async def test_create_folder_creates_the_node_and_returns_its_id(
    writer: Client[Any], backend: Backend, metrics: Metrics
) -> None:
    backend.script = [_json(payloads.created(9001, name="Reports"))]

    result = await _call(
        writer, "create_folder", {"parent_id": 2000, "name": " Reports ", "description": "Q1"}
    )

    assert (result["id"], result["name"], result["parent_id"]) == (9001, "Reports", 2000)
    assert "untrusted" in result["notice"]
    body = json.loads(parse_qs(backend.data_requests[0].content.decode())["body"][0])
    assert body == {"type": 0, "parent_id": 2000, "name": "Reports", "description": "Q1"}
    assert 'otcs_mcp_tool_calls_total{tool="create_folder",outcome="ok"} 1' in metrics.render()


@pytest.mark.parametrize(
    "arguments",
    [
        {"parent_id": 0, "name": "x"},
        {"parent_id": -2, "name": "x"},
        {"parent_id": "abc", "name": "x"},
        {"parent_id": 2000, "name": ""},
        {"parent_id": 2000, "name": "a/b"},
        {"parent_id": 2000, "name": "a\\b"},
        {"parent_id": 2000, "name": "../etc"},
        {"parent_id": 2000, "name": ".."},
        {"parent_id": 2000, "name": "a:b"},
        {"parent_id": 2000, "name": "a\x00b"},
        {"parent_id": 2000, "name": "a" * 249},
        {"parent_id": 2000, "name": "ok", "description": "line1\nline2"},
        {"parent_id": 2000, "name": "ok", "description": "x" * 1001},
        {"parent_id": 2000},
    ],
)
async def test_create_folder_rejects_invalid_input_without_calling_opentext(
    writer: Client[Any], backend: Backend, arguments: dict[str, Any]
) -> None:
    with pytest.raises(ToolError):
        await writer.call_tool("create_folder", arguments)

    assert backend.requests == []


async def test_create_folder_duplicate_name_gets_an_actionable_error(
    writer: Client[Any], backend: Backend
) -> None:
    backend.script = [httpx.Response(500, json={"error": "stack trace /opt/otcs"})]

    with pytest.raises(ToolError, match="may already exist") as info:
        await writer.call_tool("create_folder", {"parent_id": 2000, "name": "Reports"})

    assert "/opt/otcs" not in str(info.value)
    assert len(backend.data_requests) == 1


async def test_create_folder_timeout_warns_the_change_may_have_been_applied(
    writer: Client[Any], backend: Backend, sleeps: list[float]
) -> None:
    backend.script = [httpx.ReadTimeout("slow")]

    with pytest.raises(ToolError, match="may or may not have been applied"):
        await writer.call_tool("create_folder", {"parent_id": 2000, "name": "Reports"})

    assert sleeps == []
    assert len(backend.data_requests) == 1


# ----------------------------------------------------------------- upload_document


async def test_upload_document_from_text_guesses_the_media_type_from_the_name(
    writer: Client[Any], backend: Backend
) -> None:
    backend.script = [_json(payloads.created(9002, name="notes.txt", type=144))]

    result = await _call(
        writer,
        "upload_document",
        {"parent_id": 2000, "name": "notes.txt", "content_text": "héllo wörld"},
    )

    request = backend.data_requests[0]
    assert result["id"] == 9002
    assert b"Content-Type: text/plain" in request.content
    assert "héllo wörld".encode() in request.content
    assert request.headers["content-type"].startswith("multipart/form-data")


async def test_upload_document_from_base64_preserves_the_bytes(
    writer: Client[Any], backend: Backend
) -> None:
    payload = bytes(range(200))
    backend.script = [_json(payloads.created(9003, type=144))]

    await _call(
        writer,
        "upload_document",
        {
            "parent_id": 2000,
            "name": "blob.bin",
            "content_base64": base64.b64encode(payload).decode(),
        },
    )

    request = backend.data_requests[0]
    assert payload in request.content
    assert b"Content-Type: application/octet-stream" in request.content


async def test_upload_document_uses_an_explicit_media_type_over_the_guess(
    writer: Client[Any], backend: Backend
) -> None:
    backend.script = [_json(payloads.created(9004, type=144))]

    await _call(
        writer,
        "upload_document",
        {
            "parent_id": 2000,
            "name": "notes.txt",
            "content_text": "x",
            "mime_type": "Application/JSON",
        },
    )

    assert b"Content-Type: application/json" in backend.data_requests[0].content


@pytest.mark.parametrize(
    "arguments",
    [
        {"parent_id": 2000, "name": "a.txt"},  # no content
        {"parent_id": 2000, "name": "a.txt", "content_text": "x", "content_base64": "eA=="},
        {"parent_id": 2000, "name": "a.txt", "content_text": ""},
        {"parent_id": 2000, "name": "a.txt", "content_base64": "!!!"},
        {"parent_id": 2000, "name": "a.txt", "content_text": "x" * (MAX_UPLOAD + 1)},
        {"parent_id": 2000, "name": "a.txt", "content_text": "ñ" * MAX_UPLOAD},  # 2x bytes
        {"parent_id": 2000, "name": "a.txt", "content_base64": "A" * 5000},
        {"parent_id": 2000, "name": "a.txt", "content_text": "x", "mime_type": "not a type"},
        {"parent_id": 2000, "name": "a/b.txt", "content_text": "x"},
        {"parent_id": 0, "name": "a.txt", "content_text": "x"},
        {"parent_id": 2000, "name": "a.txt", "content_text": "x", "description": "a\nb"},
    ],
)
async def test_upload_document_rejects_invalid_input_without_calling_opentext(
    writer: Client[Any], backend: Backend, arguments: dict[str, Any]
) -> None:
    with pytest.raises(ToolError):
        await writer.call_tool("upload_document", arguments)

    assert backend.requests == []


async def test_upload_document_that_is_exactly_at_the_limit_is_accepted(
    writer: Client[Any], backend: Backend
) -> None:
    backend.script = [_json(payloads.created(9005, type=144))]

    result = await _call(
        writer,
        "upload_document",
        {"parent_id": 2000, "name": "a.txt", "content_text": "x" * MAX_UPLOAD},
    )

    assert result["id"] == 9005


async def test_upload_document_duplicate_name_gets_an_actionable_error(
    writer: Client[Any], backend: Backend
) -> None:
    backend.script = [httpx.Response(500)]

    with pytest.raises(ToolError, match="may already exist"):
        await writer.call_tool(
            "upload_document", {"parent_id": 2000, "name": "a.txt", "content_text": "x"}
        )


# ----------------------------------------------------------------- update_node_category


async def test_update_category_with_confirmation_changes_values_and_returns_the_previous_state(
    writer: Client[Any], backend: Backend, metrics: Metrics
) -> None:
    backend.script = [_category_get(), httpx.Response(200, json={})]

    result = await _call(
        writer,
        "update_node_category",
        {
            "node_id": 12345,
            "category_id": 9830,
            "values": {"9830_2": "new"},
            "confirm": True,
            "metadata_token": "9ec9bcab09f9963d",
        },
    )

    assert result["updated_keys"] == ["9830_2"]
    assert result["previous_state"] == [{"categories": {"9830_2": "old"}}]
    get_request, put_request = backend.data_requests
    assert (get_request.method, put_request.method) == ("GET", "PUT")
    assert parse_qs(put_request.content.decode()) == {
        "9830_2": ["new"],
        "metadata_token": ["9ec9bcab09f9963d"],
    }
    assert 'tool="update_node_category",outcome="ok"' in metrics.render()


@pytest.mark.parametrize("confirm", [False, "true", "yes", 1, "false", None])
async def test_update_category_without_a_real_true_confirmation_changes_nothing(
    writer: Client[Any], backend: Backend, confirm: object
) -> None:
    with pytest.raises(ToolError):
        await writer.call_tool(
            "update_node_category",
            {"node_id": 1, "category_id": 9830, "values": {"9830_2": "x"}, "confirm": confirm},
        )

    assert backend.requests == []


async def test_update_category_requires_the_confirm_argument_to_be_present(
    writer: Client[Any], backend: Backend
) -> None:
    with pytest.raises(ToolError):
        await writer.call_tool(
            "update_node_category",
            {"node_id": 1, "category_id": 9830, "values": {"9830_2": "x"}},
        )

    assert backend.requests == []


async def test_unconfirmed_update_explains_that_the_user_must_approve_it(
    writer: Client[Any],
) -> None:
    with pytest.raises(ToolError, match="explicitly approved"):
        await writer.call_tool(
            "update_node_category",
            {"node_id": 1, "category_id": 9830, "values": {"9830_2": "x"}, "confirm": False},
        )


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"6228_2": "other category"},
        {"9830_1": "reserved"},
        {"9830_2_3": "set row"},
        {"evil": "x"},
        {"9830_2": "x" * 1001},
        {"9830_2": "a\x00b"},
        {"9830_2": "a\nb"},
        {"9830_2": 5},
        {"9830_2": ["x"]},
    ],
)
async def test_update_category_rejects_unsafe_values_without_calling_opentext(
    writer: Client[Any], backend: Backend, values: dict[str, Any]
) -> None:
    with pytest.raises(ToolError):
        await writer.call_tool(
            "update_node_category",
            {"node_id": 1, "category_id": 9830, "values": values, "confirm": True},
        )

    assert backend.requests == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"node_id": 0},
        {"category_id": 0},
        {"metadata_token": "short"},
        {"metadata_token": "bad token!!"},
    ],
)
async def test_update_category_rejects_invalid_ids_and_tokens(
    writer: Client[Any], backend: Backend, arguments: dict[str, Any]
) -> None:
    call = {
        "node_id": 1,
        "category_id": 9830,
        "values": {"9830_2": "x"},
        "confirm": True,
        **arguments,
    }

    with pytest.raises(ToolError):
        await writer.call_tool("update_node_category", call)

    assert backend.requests == []


async def test_update_category_stops_before_writing_when_the_category_is_missing(
    writer: Client[Any], backend: Backend
) -> None:
    backend.script = [httpx.Response(400)]

    with pytest.raises(ToolError):
        await writer.call_tool(
            "update_node_category",
            {"node_id": 1, "category_id": 9830, "values": {"9830_2": "x"}, "confirm": True},
        )

    assert [r.method for r in backend.data_requests] == ["GET"]


async def test_update_category_reports_an_edit_conflict_with_guidance(
    writer: Client[Any], backend: Backend
) -> None:
    backend.script = [_category_get(), httpx.Response(409)]

    with pytest.raises(ToolError, match="edit conflict"):
        await writer.call_tool(
            "update_node_category",
            {"node_id": 1, "category_id": 9830, "values": {"9830_2": "x"}, "confirm": True},
        )


async def test_failed_writes_are_counted_as_errors_in_the_metrics(
    writer: Client[Any], backend: Backend, metrics: Metrics
) -> None:
    backend.script = [httpx.Response(403)]

    with pytest.raises(ToolError, match="lacks permission"):
        await writer.call_tool("create_folder", {"parent_id": 2000, "name": "Reports"})

    assert 'tool="create_folder",outcome="error"' in metrics.render()
