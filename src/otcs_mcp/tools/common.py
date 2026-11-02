# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Helpers shared by the tool modules."""

import logging
from collections.abc import Awaitable
from typing import Any

from fastmcp.exceptions import ToolError

from otcs_mcp.config import Settings
from otcs_mcp.errors import McpServerError
from otcs_mcp.security import validate_page_args, validate_text

logger = logging.getLogger(__name__)

READ_ONLY_ANNOTATIONS: dict[str, Any] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

NAME_FILTER_MAX_LENGTH = 200


async def guarded[T](operation: Awaitable[T]) -> T:
    """Run a service call and convert failures into tool errors (`isError` results).

    Known errors already carry actionable, sanitised messages. Anything else is
    logged with its traceback and replaced by a generic message so internals
    never reach the model.
    """
    try:
        return await operation
    except McpServerError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error while running a tool")
        raise ToolError("Unexpected internal error while talking to OpenText.") from exc


def page_arguments(
    settings: Settings, page: object, limit: object, *, default_limit: int | None = None
) -> tuple[int, int]:
    """Validate pagination arguments, clamping the limit to MCP_MAX_RESPONSE_ITEMS."""
    try:
        return validate_page_args(
            page, limit, max_items=settings.mcp_max_response_items, default_limit=default_limit
        )
    except McpServerError as exc:
        raise ToolError(str(exc)) from exc


def optional_text(value: object, field: str) -> str | None:
    """Validate an optional text filter; `None` stays `None`."""
    if value is None:
        return None
    try:
        return validate_text(value, field=field, max_length=NAME_FILTER_MAX_LENGTH)
    except McpServerError as exc:
        raise ToolError(str(exc)) from exc
