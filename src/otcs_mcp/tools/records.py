# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Read-only Records Management tools."""

from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from otcs_mcp.config import Settings
from otcs_mcp.errors import McpServerError
from otcs_mcp.models import (
    HoldDetail,
    HoldSummary,
    NodeHold,
    NodeRsi,
    PagedResult,
    RmClassification,
)
from otcs_mcp.security import validate_node_id
from otcs_mcp.services.records import RecordsService
from otcs_mcp.tools.common import READ_ONLY_ANNOTATIONS, guarded, page_arguments

NodeId = Annotated[int, Field(ge=1, description="OpenText node ID (positive integer).")]
HoldId = Annotated[int, Field(ge=1, description="Hold ID (positive integer).")]
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


def register_record_tools(mcp: FastMCP, service: RecordsService, settings: Settings) -> None:
    """Register the read-only Records Management tools on ``mcp``."""

    @mcp.tool(
        name="get_record_classification",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "Get the Records Management classification of a node: file number, RSI, status, "
            "review dates, officer and manager. Use it to find out whether a document is a "
            "managed record and what retention rules apply. Requires Records Management "
            "access for the service account."
        ),
    )
    async def get_record_classification(node_id: NodeId) -> PagedResult[RmClassification]:
        return await guarded(
            service.node_classifications(
                _id(node_id, "node_id"), limit=settings.mcp_max_response_items
            )
        )

    @mcp.tool(
        name="list_node_holds",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "List the holds applied to a node (hold name, type, who applied it and when, "
            "whether it is active). Use it before assuming a record can be disposed of or "
            "changed. Not paginated."
        ),
    )
    async def list_node_holds(node_id: NodeId) -> PagedResult[NodeHold]:
        return await guarded(
            service.node_holds(_id(node_id, "node_id"), limit=settings.mcp_max_response_items)
        )

    @mcp.tool(
        name="list_node_rsis",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "List the Retention Schedule Identifiers (RSI) assigned to a node through its "
            "classifications. Not paginated."
        ),
    )
    async def list_node_rsis(node_id: NodeId) -> PagedResult[NodeRsi]:
        return await guarded(
            service.node_rsis(_id(node_id, "node_id"), limit=settings.mcp_max_response_items)
        )

    @mcp.tool(
        name="list_holds",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "List the holds defined in Records Management. Paginated; if has_more is true, "
            "call again with next_page. Requires Records Management access."
        ),
    )
    async def list_holds(page: PageArg = None, limit: LimitArg = None) -> PagedResult[HoldSummary]:
        page_number, page_size = page_arguments(settings, page, limit)
        return await guarded(service.list_holds(page=page_number, limit=page_size))

    @mcp.tool(
        name="get_hold",
        annotations=READ_ONLY_ANNOTATIONS,
        description="Get one Records Management hold by its ID (name, type, comment, dates).",
    )
    async def get_hold(hold_id: HoldId) -> HoldDetail:
        return await guarded(service.get_hold(_id(hold_id, "hold_id")))

    @mcp.tool(
        name="list_rsis",
        annotations=READ_ONLY_ANNOTATIONS,
        description=(
            "List Retention Schedule Identifiers (RSI) with their schedules. OpenText leaves "
            "the entry layout open, so entries are returned as bounded key/value objects. "
            "Paginated; if has_more is true, call again with next_page."
        ),
    )
    async def list_rsis(
        page: PageArg = None, limit: LimitArg = None
    ) -> PagedResult[dict[str, Any]]:
        page_number, page_size = page_arguments(settings, page, limit)
        return await guarded(service.list_rsis(page=page_number, limit=page_size))
