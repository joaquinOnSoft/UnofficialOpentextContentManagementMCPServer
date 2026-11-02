# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Tools for Business Workspaces."""

from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from otcs_mcp.config import Settings
from otcs_mcp.errors import McpServerError
from otcs_mcp.models import (
    MemberSummary,
    PagedResult,
    RoleSummary,
    WorkspaceDetail,
    WorkspaceSummary,
    WorkspaceTypeSummary,
)
from otcs_mcp.security import validate_node_id
from otcs_mcp.services.workspaces import WorkspaceService
from otcs_mcp.tools.common import READ_ONLY_ANNOTATIONS, guarded, optional_text, page_arguments

WorkspaceId = Annotated[int, Field(ge=1, description="Business Workspace node ID.")]
PageArg = Annotated[int | None, Field(ge=1, description="1-based page number. Defaults to 1.")]
LimitArg = Annotated[
    int | None,
    Field(ge=1, description="Items per page. Capped by the server (MCP_MAX_RESPONSE_ITEMS)."),
]


def _id(value: object, field: str) -> int:
    try:
        return validate_node_id(value, field)
    except McpServerError as exc:
        raise ToolError(str(exc)) from exc


def register_workspace_tools(mcp: FastMCP, service: WorkspaceService, settings: Settings) -> None:
    """Register the read-only Business Workspace tools on ``mcp``."""

    @mcp.tool(
        name="search_business_workspaces",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "List Business Workspaces the service account can see, optionally filtered by "
            "name and workspace type. Use it to find the workspace of a customer, case or "
            "contract. Use list_workspace_types to discover type IDs. Paginated; if has_more "
            "is true, call again with next_page."
        ),
    )
    async def search_business_workspaces(
        name_filter: Annotated[
            str | None, Field(max_length=200, description="Filter on workspace name.")
        ] = None,
        workspace_type_id: Annotated[
            int | None, Field(ge=1, description="Restrict to one workspace type.")
        ] = None,
        page: PageArg = None,
        limit: LimitArg = None,
    ) -> PagedResult[WorkspaceSummary]:
        name = optional_text(name_filter, "name_filter")
        type_id = None if workspace_type_id is None else _id(workspace_type_id, "workspace_type_id")
        page_number, page_size = page_arguments(settings, page, limit)
        return await guarded(
            service.search(
                name_filter=name, workspace_type_id=type_id, page=page_number, limit=page_size
            )
        )

    @mcp.tool(
        name="get_business_workspace",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Get the details of one Business Workspace: name, type, owner, dates and the "
            "business object references (at most 20). Does not change the user's "
            "recently-accessed list."
        ),
    )
    async def get_business_workspace(workspace_id: WorkspaceId) -> WorkspaceDetail:
        return await guarded(service.get(_id(workspace_id, "workspace_id")))

    @mcp.tool(
        name="list_workspace_related_items",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "List the workspaces related to a Business Workspace (its parents and/or "
            "children). Use it to navigate relationships between workspaces. Paginated."
        ),
    )
    async def list_workspace_related_items(
        workspace_id: WorkspaceId,
        relation: Annotated[
            Literal["parent", "child"] | None,
            Field(description="Only parents or only children. Omit for both."),
        ] = None,
        name_filter: Annotated[
            str | None, Field(max_length=200, description="Filter on related item name.")
        ] = None,
        page: PageArg = None,
        limit: LimitArg = None,
    ) -> PagedResult[WorkspaceSummary]:
        validated_id = _id(workspace_id, "workspace_id")
        name = optional_text(name_filter, "name_filter")
        page_number, page_size = page_arguments(settings, page, limit)
        return await guarded(
            service.related_items(
                validated_id,
                relation=relation,
                name_filter=name,
                page=page_number,
                limit=page_size,
            )
        )

    @mcp.tool(
        name="list_workspace_members",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "List the users and groups that are members of a Business Workspace, with their "
            "leader flag. Paginated."
        ),
    )
    async def list_workspace_members(
        workspace_id: WorkspaceId,
        include_groups: Annotated[
            bool, Field(description="Include groups as well as users.")
        ] = True,
        page: PageArg = None,
        limit: LimitArg = None,
    ) -> PagedResult[MemberSummary]:
        validated_id = _id(workspace_id, "workspace_id")
        page_number, page_size = page_arguments(settings, page, limit)
        return await guarded(
            service.members(
                validated_id, include_groups=include_groups, page=page_number, limit=page_size
            )
        )

    @mcp.tool(
        name="list_workspace_roles",
        annotations=READ_ONLY_ANNOTATIONS,
        description="List the roles defined in a Business Workspace (not paginated).",
    )
    async def list_workspace_roles(workspace_id: WorkspaceId) -> PagedResult[RoleSummary]:
        validated_id = _id(workspace_id, "workspace_id")
        return await guarded(service.roles(validated_id, limit=settings.mcp_max_response_items))

    @mcp.tool(
        name="list_workspace_types",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "List the Business Workspace types configured in OpenText (ID and name). Use it "
            "to find the type ID for search_business_workspaces. Not paginated."
        ),
    )
    async def list_workspace_types() -> PagedResult[WorkspaceTypeSummary]:
        return await guarded(service.types(limit=settings.mcp_max_response_items))
