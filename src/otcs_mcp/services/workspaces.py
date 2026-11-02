# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Business Workspace operations (Business Workspaces REST API v2)."""

from typing import Any

from otcs_mcp.client import OtcsClient
from otcs_mcp.config import Settings
from otcs_mcp.errors import OtcsResponseError
from otcs_mcp.models import (
    MemberSummary,
    PagedResult,
    RoleSummary,
    WorkspaceDetail,
    WorkspaceSummary,
    WorkspaceTypeSummary,
)
from otcs_mcp.services.shapes import (
    DESCRIPTION_LIMIT,
    NAME_LIMIT,
    as_bool,
    as_dict,
    as_int,
    build_page,
    clean_text,
    item_data,
    item_properties,
    paging_of,
    result_items,
)

RELATION_TYPES = ("parent", "child")
MAX_REFERENCES = 20


def _workspace_summary(item: dict[str, Any]) -> WorkspaceSummary | None:
    props = item_properties(item)
    workspace_id = as_int(props.get("id"))
    if workspace_id is None:
        return None
    return WorkspaceSummary(
        id=workspace_id,
        name=clean_text(props.get("name"), NAME_LIMIT) or "",
        workspace_type_id=as_int(props.get("wnf_wksp_type_id")),
        type_name=clean_text(
            props.get("wnf_wksp_type_id_formatted") or props.get("type_name"), 100
        ),
        parent_id=as_int(props.get("parent_id")),
        description=clean_text(props.get("description"), DESCRIPTION_LIMIT),
        create_date=clean_text(props.get("create_date"), 40),
        modify_date=clean_text(props.get("modify_date"), 40),
    )


def _member(item: dict[str, Any]) -> MemberSummary:
    props = item_properties(item)
    return MemberSummary(
        id=as_int(props.get("id")),
        name=clean_text(props.get("name"), NAME_LIMIT),
        type=as_int(props.get("type")),
        type_formatted=clean_text(props.get("type_formatted"), 50),
        leader=as_bool(props.get("leader")),
        inherited_from_id=as_int(props.get("inherited_from_id")),
    )


def _role(item: dict[str, Any]) -> RoleSummary:
    props = item_properties(item)
    return RoleSummary(
        id=as_int(props.get("id")),
        name=clean_text(props.get("name"), NAME_LIMIT),
        description=clean_text(props.get("description"), DESCRIPTION_LIMIT),
        leader=as_bool(props.get("leader")),
        perms=as_int(props.get("perms")),
    )


def _workspace_type(item: dict[str, Any]) -> WorkspaceTypeSummary:
    data = item_data(item)
    props = as_dict(data.get("properties"))
    info = as_dict(data.get("wksp_info"))
    return WorkspaceTypeSummary(
        id=as_int(props.get("wksp_type_id")),
        name=clean_text(props.get("wksp_type_name"), NAME_LIMIT),
        rm_enabled=as_bool(props.get("rm_enabled")),
        in_use=as_bool(info.get("in_use")),
    )


class WorkspaceService:
    """Read-only operations on Business Workspaces."""

    def __init__(self, client: OtcsClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def search(
        self,
        *,
        name_filter: str | None,
        workspace_type_id: int | None,
        page: int,
        limit: int,
    ) -> PagedResult[WorkspaceSummary]:
        """List workspaces, optionally filtered by name and type."""
        payload = await self._client.get_json(
            "/v2/businessworkspaces",
            params={
                "where_name": name_filter,
                "where_workspace_type_id": workspace_type_id,
                "page": page,
                "limit": limit,
            },
        )
        return build_page(
            result_items(payload),
            _workspace_summary,
            page=page,
            limit=limit,
            paging=paging_of(payload),
        )

    async def get(self, workspace_id: int) -> WorkspaceDetail:
        """Return the details of one workspace without touching the 'recently accessed' list."""
        payload = await self._client.get_json(
            f"/v2/businessworkspaces/{workspace_id}",
            params={"add_recently_accessed": False},  # keep the tool strictly read-only
        )
        items = result_items(payload)
        data = item_data(items[0]) if items else {}
        props = as_dict(data.get("properties"))
        business = as_dict(data.get("business_properties"))
        base = _workspace_summary({"data": {"properties": props}})
        if base is None:
            raise OtcsResponseError("OpenText returned a workspace without an id")
        references = data.get("workspace_references")
        return WorkspaceDetail(
            **base.model_dump(exclude={"workspace_type_id", "type_name"}),
            workspace_type_id=as_int(business.get("workspace_type_id")),
            type_name=clean_text(props.get("type_name"), 100),
            workspace_type_name=clean_text(business.get("workspace_type_name"), NAME_LIMIT),
            rm_enabled=as_bool(props.get("rm_enabled")),
            owner_user_id=as_int(props.get("owner_user_id")),
            create_user_id=as_int(props.get("create_user_id")),
            modify_user_id=as_int(props.get("modify_user_id")),
            business_references=[
                ref
                for ref in (references if isinstance(references, list) else [])
                if isinstance(ref, dict)
            ][:MAX_REFERENCES],
        )

    async def related_items(
        self,
        workspace_id: int,
        *,
        relation: str | None,
        name_filter: str | None,
        page: int,
        limit: int,
    ) -> PagedResult[WorkspaceSummary]:
        """List workspaces related to a workspace (parents and/or children)."""
        payload = await self._client.get_json(
            f"/v2/businessworkspaces/{workspace_id}/relateditems",
            params={
                "where_rel_types": [relation] if relation else None,
                "where_name": name_filter,
                "page": page,
                "limit": limit,
            },
        )
        return build_page(
            result_items(payload),
            _workspace_summary,
            page=page,
            limit=limit,
            paging=paging_of(payload),
        )

    async def members(
        self, workspace_id: int, *, include_groups: bool, page: int, limit: int
    ) -> PagedResult[MemberSummary]:
        """List the members of a workspace."""
        payload = await self._client.get_json(
            f"/v2/businessworkspaces/{workspace_id}/members",
            params={"include_groups": include_groups, "page": page, "limit": limit},
        )
        return build_page(
            result_items(payload), _member, page=page, limit=limit, paging=paging_of(payload)
        )

    async def roles(self, workspace_id: int, *, limit: int) -> PagedResult[RoleSummary]:
        """List the roles of a workspace (the endpoint is not paginated)."""
        payload = await self._client.get_json(f"/v2/businessworkspaces/{workspace_id}/roles")
        return build_page(
            result_items(payload), _role, page=1, limit=limit, paging={}, paginated=False
        )

    async def types(self, *, limit: int) -> PagedResult[WorkspaceTypeSummary]:
        """List Business Workspace types (the endpoint is not paginated)."""
        payload = await self._client.get_json("/v2/businessworkspacetypes")
        return build_page(
            result_items(payload),
            _workspace_type,
            page=1,
            limit=limit,
            paging={},
            paginated=False,
        )
