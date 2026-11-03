# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from otcs_mcp.config import Settings
from otcs_mcp.server import TOOL_NAMES, build_server
from otcs_mcp.services.nodes import NodeService
from tests import payloads
from tests.conftest import Backend, ClientFactory

EXPECTED_TOOLS = [
    "search_nodes",
    "get_node",
    "list_folder_children",
    "get_node_categories",
    "read_document_text",
    "search_business_workspaces",
    "get_business_workspace",
    "list_workspace_related_items",
    "list_workspace_members",
    "list_workspace_roles",
    "list_workspace_types",
]


@pytest.fixture
def mcp(settings: Settings, make_client: ClientFactory) -> FastMCP:
    return build_server(settings, client=make_client())


@pytest.fixture
async def mcp_client(mcp: FastMCP) -> AsyncIterator[Client[Any]]:
    async with Client(mcp) as client:
        yield client


def _json(payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


async def _call(client: Client[Any], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(name, arguments)
    assert result.structured_content is not None
    return dict(result.structured_content)


# ----------------------------------------------------------------- registration


async def test_exactly_the_expected_tools_are_registered(mcp_client: Client[Any]) -> None:
    tools = await mcp_client.list_tools()

    assert [tool.name for tool in tools] == EXPECTED_TOOLS


async def test_tool_names_constant_matches_the_registered_tools(mcp_client: Client[Any]) -> None:
    assert [tool.name for tool in await mcp_client.list_tools()] == list(TOOL_NAMES)


async def test_every_tool_is_annotated_read_only_and_declares_an_output_schema(
    mcp_client: Client[Any],
) -> None:
    for tool in await mcp_client.list_tools():
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name
        assert tool.annotations.idempotent_hint is True, tool.name
        assert tool.annotations.open_world_hint is False, tool.name
        assert tool.output_schema is not None, tool.name
        assert tool.description is not None
        assert len(tool.description) > 40, tool.name


async def test_input_schemas_are_strict_with_required_arguments_and_enums(
    mcp_client: Client[Any],
) -> None:
    tools = {tool.name: tool for tool in await mcp_client.list_tools()}

    search = tools["search_nodes"].input_schema
    assert search["additionalProperties"] is False
    assert search["required"] == ["query"]
    assert search["properties"]["mode"]["enum"] == ["allwords", "anywords", "exactphrase"]
    assert "complexquery" not in str(search)
    assert tools["get_node"].input_schema["properties"]["node_id"]["minimum"] == 1
    for tool in tools.values():
        for prop in tool.input_schema["properties"].values():
            assert prop.get("description"), f"{tool.name} has an undocumented parameter"


async def test_tool_order_is_deterministic_across_listings_and_builds(
    settings: Settings, make_client: ClientFactory
) -> None:
    orders = []
    for _ in range(2):
        async with Client(build_server(settings, client=make_client())) as client:
            orders.append([t.name for t in await client.list_tools()])
            orders.append([t.name for t in await client.list_tools()])

    assert orders[0] == orders[1] == orders[2] == orders[3]


async def test_usage_guide_resource_is_registered_and_warns_about_untrusted_data(
    mcp_client: Client[Any],
) -> None:
    resources = await mcp_client.list_resources()
    contents = await mcp_client.read_resource("otcs-mcp://usage-guide")

    assert [str(r.uri) for r in resources] == ["otcs-mcp://usage-guide"]
    assert "untrusted" in contents[0].text  # type: ignore[union-attr]


async def test_prompts_are_registered_and_render_instructions(mcp_client: Client[Any]) -> None:
    prompts = await mcp_client.list_prompts()
    explore = await mcp_client.get_prompt("explore_folder", {"node_id": "2000"})
    find = await mcp_client.get_prompt("find_documents", {"topic": "supplier contracts"})

    assert sorted(p.name for p in prompts) == ["explore_folder", "find_documents"]
    assert "folder 2000" in explore.messages[0].content.text  # type: ignore[union-attr]
    assert "supplier contracts" in find.messages[0].content.text  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("prompt", "arguments"),
    [
        ("explore_folder", {"node_id": "0"}),
        ("explore_folder", {"node_id": "-4"}),
        ("find_documents", {"topic": 'x" OR OTSubType:144'}),
        ("find_documents", {"topic": "   "}),
    ],
)
async def test_prompts_reject_invalid_arguments(
    mcp_client: Client[Any], prompt: str, arguments: dict[str, str]
) -> None:
    with pytest.raises(Exception, match=r"(?i)invalid|error"):
        await mcp_client.get_prompt(prompt, arguments)


# ----------------------------------------------------------------- tool calls


async def test_get_node_returns_structured_content_with_untrusted_data_notice(
    mcp_client: Client[Any], backend: Backend
) -> None:
    backend.script = [_json(payloads.single(payloads.node_props(2000)))]

    result = await _call(mcp_client, "get_node", {"node_id": 2000})

    assert result["id"] == 2000
    assert result["name"] == "Enterprise"
    assert "untrusted" in result["notice"]


async def test_search_nodes_posts_plain_words_and_returns_a_page(
    mcp_client: Client[Any], backend: Backend
) -> None:
    hit = payloads.node_props(9, name="Contract.docx", container=False)
    backend.script = [_json(payloads.listing([hit], total_count=1, page_total=1))]

    result = await _call(mcp_client, "search_nodes", {"query": "supplier contract"})

    form = parse_qs(backend.data_requests[0].content.decode())
    assert form["where"] == ["supplier contract"]
    assert form["lookfor"] == ["allwords"]
    assert form["within"] == ["all"]
    assert result["items"][0]["id"] == 9
    assert result["has_more"] is False
    assert result["truncated"] is False


@pytest.mark.parametrize(
    "query",
    [
        'name:"x"',
        "a OR (b)",
        "OTSubType: 144",
        "x[1]",
        "a\\b",
        "a<b>",
        "a=b",
        "tilde~",
        "p|q",
        "c^2",
    ],
)
async def test_search_nodes_rejects_query_language_characters_without_calling_opentext(
    mcp_client: Client[Any], backend: Backend, query: str
) -> None:
    with pytest.raises(ToolError, match="plain words"):
        await mcp_client.call_tool("search_nodes", {"query": query})

    assert backend.requests == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": ""},
        {"query": "x" * 201},
        {"query": "ok", "mode": "complexquery"},
        {"query": "ok", "scope": "everything"},
        {"query": "ok", "page": 0},
        {"query": "ok", "limit": 0},
    ],
)
async def test_search_nodes_rejects_invalid_arguments(
    mcp_client: Client[Any], backend: Backend, arguments: dict[str, Any]
) -> None:
    with pytest.raises(ToolError):
        await mcp_client.call_tool("search_nodes", arguments)

    assert backend.requests == []


@pytest.mark.parametrize("node_id", [0, -5, "abc", 1.5, None])
async def test_node_tools_reject_invalid_node_ids_without_calling_opentext(
    mcp_client: Client[Any], backend: Backend, node_id: object
) -> None:
    for tool in ("get_node", "list_folder_children", "get_node_categories", "read_document_text"):
        with pytest.raises(ToolError):
            await mcp_client.call_tool(tool, {"node_id": node_id})

    assert backend.requests == []


async def test_list_folder_children_clamps_limit_to_server_maximum(
    mcp_client: Client[Any], backend: Backend
) -> None:
    backend.script = [_json(payloads.listing([payloads.node_props(1)], page_total=1))]

    result = await _call(mcp_client, "list_folder_children", {"node_id": 2000, "limit": 9999})

    assert backend.data_requests[0].url.params["limit"] == "50"
    assert result["limit"] == 50


async def test_list_folder_children_builds_sort_and_filters(
    mcp_client: Client[Any], backend: Backend
) -> None:
    backend.script = [_json(payloads.listing([], page_total=1))]

    await _call(
        mcp_client,
        "list_folder_children",
        {
            "node_id": 2000,
            "kind": "containers",
            "name_filter": " Contracts ",
            "sort_by": "modify_date",
            "sort_direction": "desc",
            "page": 2,
            "limit": 5,
        },
    )

    params = backend.data_requests[0].url.params
    assert params["sort"] == "desc_modify_date"
    assert params["where_name"] == "Contracts"
    assert params.get_list("where_type") == ["-1"]
    assert (params["page"], params["limit"]) == ("2", "5")


@pytest.mark.parametrize(
    "arguments",
    [
        {"kind": "everything"},
        {"sort_by": "password"},
        {"sort_direction": "sideways"},
        {"name_filter": "x" * 201},
        {"name_filter": "a\nb"},
    ],
)
async def test_list_folder_children_rejects_invalid_options(
    mcp_client: Client[Any], backend: Backend, arguments: dict[str, Any]
) -> None:
    with pytest.raises(ToolError):
        await mcp_client.call_tool("list_folder_children", {"node_id": 2000, **arguments})

    assert backend.requests == []


async def test_read_document_text_returns_wrapped_text_and_honours_server_cap(
    settings: Settings, make_client: ClientFactory, backend: Backend
) -> None:
    capped = settings.model_copy(update={"mcp_max_content_bytes": 2000})
    backend.script = [
        _json(payloads.versions(1, "text/plain")),
        httpx.Response(200, content=b"a" * 5000),
    ]
    async with Client(build_server(capped, client=make_client())) as client:
        result = await _call(client, "read_document_text", {"node_id": 42, "max_bytes": 1_000_000})

    assert result["available"] is True
    assert result["truncated"] is True
    assert result["bytes_returned"] == 2000
    assert result["text"].startswith('<untrusted_content source="otcs" node_id="42">')


async def test_read_document_text_rejects_max_bytes_below_minimum(
    mcp_client: Client[Any], backend: Backend
) -> None:
    with pytest.raises(ToolError):
        await mcp_client.call_tool("read_document_text", {"node_id": 1, "max_bytes": 10})

    assert backend.requests == []


async def test_get_node_categories_returns_entries(
    mcp_client: Client[Any], backend: Backend
) -> None:
    backend.script = [_json(payloads.categories([{"9830_2": "ACME"}]))]

    result = await _call(mcp_client, "get_node_categories", {"node_id": 2000})

    assert result["categories"] == [{"9830_2": "ACME"}]
    assert result["truncated"] is False


async def test_workspace_tools_return_expected_structures(
    mcp_client: Client[Any], backend: Backend
) -> None:
    backend.script = [
        _json(payloads.listing([payloads.workspace_props(5000)], page_total=1, paging_at_top=True)),
        _json(payloads.workspace_detail(payloads.workspace_props(5000))),
        _json(payloads.listing([payloads.workspace_props(6000)], page_total=1, paging_at_top=True)),
        _json(payloads.members([{"id": 1, "name": "ana", "leader": True}], page_total=1)),
        _json(payloads.roles([{"id": 1, "name": "Lead", "perms": 6}])),
        _json(payloads.workspace_types([{"wksp_type_id": 7, "wksp_type_name": "Customer"}])),
    ]

    search = await _call(
        mcp_client, "search_business_workspaces", {"name_filter": "ACME", "workspace_type_id": 7}
    )
    detail = await _call(mcp_client, "get_business_workspace", {"workspace_id": 5000})
    related = await _call(
        mcp_client, "list_workspace_related_items", {"workspace_id": 5000, "relation": "child"}
    )
    members = await _call(
        mcp_client, "list_workspace_members", {"workspace_id": 5000, "include_groups": False}
    )
    roles = await _call(mcp_client, "list_workspace_roles", {"workspace_id": 5000})
    types = await _call(mcp_client, "list_workspace_types", {})

    assert search["items"][0]["id"] == 5000
    assert detail["workspace_type_name"] == "Customer"
    assert related["items"][0]["id"] == 6000
    assert members["items"][0]["name"] == "ana"
    assert roles["items"][0]["name"] == "Lead"
    assert types["items"][0]["id"] == 7
    assert backend.data_requests[1].url.params["add_recently_accessed"] == "false"


@pytest.mark.parametrize("workspace_id", [0, -1, "x"])
async def test_workspace_tools_reject_invalid_ids_without_calling_opentext(
    mcp_client: Client[Any], backend: Backend, workspace_id: object
) -> None:
    for tool in (
        "get_business_workspace",
        "list_workspace_related_items",
        "list_workspace_members",
        "list_workspace_roles",
    ):
        with pytest.raises(ToolError):
            await mcp_client.call_tool(tool, {"workspace_id": workspace_id})
    with pytest.raises(ToolError):
        await mcp_client.call_tool(
            "search_business_workspaces", {"workspace_type_id": workspace_id}
        )

    assert backend.requests == []


async def test_workspace_filters_reject_unsafe_text(
    mcp_client: Client[Any], backend: Backend
) -> None:
    with pytest.raises(ToolError):
        await mcp_client.call_tool("search_business_workspaces", {"name_filter": "a\x00b"})
    with pytest.raises(ToolError):
        await mcp_client.call_tool(
            "list_workspace_related_items", {"workspace_id": 1, "relation": "sibling"}
        )

    assert backend.requests == []


# ----------------------------------------------------------------- error handling


async def test_opentext_not_found_becomes_actionable_tool_error_without_internals(
    mcp_client: Client[Any], backend: Backend
) -> None:
    backend.script = [_json({"error": "stack at /opt/otcs/x.oscript"}, 404)]

    with pytest.raises(ToolError) as info:
        await mcp_client.call_tool("get_node", {"node_id": 12345})

    message = str(info.value)
    assert "not found" in message
    for leak in ("otcs.example.com", "oscript", "ticket-"):
        assert leak not in message


@pytest.mark.parametrize(
    ("status", "fragment"),
    [(403, "lacks permission"), (400, "invalid"), (503, "internal error"), (429, "rate limiting")],
)
async def test_opentext_http_errors_are_mapped_to_clear_messages(
    mcp_client: Client[Any], backend: Backend, status: int, fragment: str
) -> None:
    backend.script = [httpx.Response(status)] * 3

    with pytest.raises(ToolError, match=fragment):
        await mcp_client.call_tool("get_node", {"node_id": 1})


async def test_unexpected_exceptions_are_masked_and_logged_with_traceback(
    mcp_client: Client[Any], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom(self: NodeService, node_id: int) -> None:
        raise RuntimeError("secret /opt/internal/path password=hunter2")

    monkeypatch.setattr(NodeService, "get_node", boom)
    caplog.set_level(logging.ERROR)

    with pytest.raises(ToolError) as info:
        await mcp_client.call_tool("get_node", {"node_id": 1})

    assert "hunter2" not in str(info.value)
    assert "/opt/internal" not in str(info.value)
    assert "Unexpected internal error" in str(info.value)
    assert "Unexpected error while running a tool" in caplog.text


async def test_server_closes_the_opentext_client_on_shutdown(
    settings: Settings, make_client: ClientFactory
) -> None:
    client = make_client()
    async with Client(build_server(settings, client=client)):
        assert not client._http.is_closed

    assert client._http.is_closed
