# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

import httpx
import pytest

from otcs_mcp.config import Settings
from otcs_mcp.errors import OtcsResponseError
from otcs_mcp.services.workspaces import MAX_REFERENCES, WorkspaceService
from tests import payloads
from tests.conftest import Backend, ClientFactory


@pytest.fixture
def service(make_client: ClientFactory, settings: Settings) -> WorkspaceService:
    return WorkspaceService(make_client(), settings)


def _json(payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload)


async def test_search_sends_filters_and_maps_workspaces(
    service: WorkspaceService, backend: Backend
) -> None:
    rows = [payloads.workspace_props(5000), payloads.workspace_props(5001, name="Customer BETA")]
    backend.script = [
        _json(payloads.listing(rows, total_count=2, page_total=1, paging_at_top=True))
    ]

    result = await service.search(name_filter="Customer", workspace_type_id=7, page=1, limit=10)

    params = backend.data_requests[0].url.params
    assert params["where_name"] == "Customer"
    assert params["where_workspace_type_id"] == "7"
    assert (params["page"], params["limit"]) == ("1", "10")
    assert [w.id for w in result.items] == [5000, 5001]
    assert result.items[0].workspace_type_id == 7
    assert result.items[0].type_name == "Customer"
    assert result.total_count == 2
    assert result.has_more is False


async def test_search_omits_filters_when_none_given_and_skips_entries_without_id(
    service: WorkspaceService, backend: Backend
) -> None:
    backend.script = [_json(payloads.listing([{"name": "no id"}], paging_at_top=True))]

    result = await service.search(name_filter=None, workspace_type_id=None, page=1, limit=10)

    params = backend.data_requests[0].url.params
    assert "where_name" not in params
    assert "where_workspace_type_id" not in params
    assert result.items == []


async def test_get_workspace_never_updates_recently_accessed_list(
    service: WorkspaceService, backend: Backend
) -> None:
    detail = payloads.workspace_detail(
        payloads.workspace_props(5000, rm_enabled=True),
        references=[{"bo_id": "C-1", "bo_type": "KNA1"}, "junk"],
    )
    backend.script = [_json(detail)]

    workspace = await service.get(5000)

    request = backend.data_requests[0]
    assert request.url.path.endswith("/v2/businessworkspaces/5000")
    assert request.url.params["add_recently_accessed"] == "false"
    assert (workspace.id, workspace.name) == (5000, "Customer ACME")
    assert workspace.workspace_type_id == 7
    assert workspace.workspace_type_name == "Customer"
    assert workspace.rm_enabled is True
    assert workspace.owner_user_id == 1000
    assert workspace.business_references == [{"bo_id": "C-1", "bo_type": "KNA1"}]
    assert workspace.notice


async def test_get_workspace_caps_business_references(
    service: WorkspaceService, backend: Backend
) -> None:
    refs = [{"bo_id": str(i)} for i in range(MAX_REFERENCES + 10)]
    backend.script = [_json(payloads.workspace_detail(payloads.workspace_props(1), refs))]

    assert len((await service.get(1)).business_references) == MAX_REFERENCES


async def test_get_workspace_handles_missing_references_section(
    service: WorkspaceService, backend: Backend
) -> None:
    body = {"results": {"data": {"properties": payloads.workspace_props(1)}}}
    backend.script = [_json(body)]

    assert (await service.get(1)).business_references == []


@pytest.mark.parametrize(
    "body", [{"results": {"data": {"properties": {"name": "x"}}}}, {"results": []}]
)
async def test_get_workspace_raises_when_reply_has_no_id(
    service: WorkspaceService, backend: Backend, body: dict[str, object]
) -> None:
    backend.script = [_json(body)]

    with pytest.raises(OtcsResponseError, match="without an id"):
        await service.get(1)


async def test_related_items_filters_by_relation_type_and_name(
    service: WorkspaceService, backend: Backend
) -> None:
    backend.script = [
        _json(payloads.listing([payloads.workspace_props(6000)], page_total=2, paging_at_top=True))
    ]

    result = await service.related_items(
        5000, relation="child", name_filter="Order", page=1, limit=5
    )

    request = backend.data_requests[0]
    assert request.url.path.endswith("/v2/businessworkspaces/5000/relateditems")
    assert request.url.params.get_list("where_rel_types") == ["child"]
    assert request.url.params["where_name"] == "Order"
    assert result.has_more is True
    assert result.next_page == 2


async def test_related_items_omits_relation_filter_when_not_given(
    service: WorkspaceService, backend: Backend
) -> None:
    backend.script = [_json(payloads.listing([], paging_at_top=True))]

    await service.related_items(5000, relation=None, name_filter=None, page=1, limit=5)

    assert "where_rel_types" not in backend.data_requests[0].url.params


async def test_members_maps_users_and_groups_and_sends_include_groups(
    service: WorkspaceService, backend: Backend
) -> None:
    entries = [
        {"id": 1, "name": "ana", "type": 0, "type_formatted": "User", "leader": True},
        {"id": 2, "name": "sales", "type": 1, "type_formatted": "Group", "inherited_from_id": 9},
    ]
    backend.script = [_json(payloads.members(entries, page_total=1))]

    result = await service.members(5000, include_groups=False, page=1, limit=10)

    params = backend.data_requests[0].url.params
    assert params["include_groups"] == "false"
    assert [(m.name, m.leader, m.inherited_from_id) for m in result.items] == [
        ("ana", True, None),
        ("sales", None, 9),
    ]
    assert result.has_more is False


async def test_roles_are_returned_without_pagination_and_cut_to_limit(
    service: WorkspaceService, backend: Backend
) -> None:
    entries = [
        {"id": i, "name": f"role{i}", "description": "d", "leader": False, "perms": 6}
        for i in range(5)
    ]
    backend.script = [_json(payloads.roles(entries))]

    result = await service.roles(5000, limit=3)

    assert [r.name for r in result.items] == ["role0", "role1", "role2"]
    assert result.truncated is True
    assert result.has_more is False
    assert result.next_page is None
    assert backend.data_requests[0].url.path.endswith("/v2/businessworkspaces/5000/roles")


async def test_types_are_mapped_with_in_use_flag(
    service: WorkspaceService, backend: Backend
) -> None:
    entries = [{"wksp_type_id": 7, "wksp_type_name": "Customer", "rm_enabled": False}]
    backend.script = [_json(payloads.workspace_types(entries))]

    result = await service.types(limit=50)

    assert [(t.id, t.name, t.rm_enabled, t.in_use) for t in result.items] == [
        (7, "Customer", False, True)
    ]
    assert result.truncated is False
    assert backend.data_requests[0].url.path.endswith("/v2/businessworkspacetypes")
