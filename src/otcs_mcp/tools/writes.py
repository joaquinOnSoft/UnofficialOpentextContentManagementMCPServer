# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Write tools. They exist only when ``MCP_READ_ONLY=false``.

* ``create_folder`` and ``upload_document`` add new nodes and never overwrite anything.
* ``update_node_category`` overwrites existing metadata: it is annotated
  ``destructiveHint`` and refuses to run without an explicit ``confirm=true``.
"""

import mimetypes
from collections.abc import Callable
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from otcs_mcp.config import Settings
from otcs_mcp.errors import ConfigError, McpServerError
from otcs_mcp.models import CategoryUpdateResult, CreatedNode
from otcs_mcp.security import (
    MAX_CATEGORY_VALUES,
    MAX_DESCRIPTION_LENGTH,
    MAX_NODE_NAME_LENGTH,
    decode_upload,
    validate_category_values,
    validate_confirmation,
    validate_description,
    validate_metadata_token,
    validate_mime_type,
    validate_node_id,
    validate_node_name,
)
from otcs_mcp.services.writes import WriteService
from otcs_mcp.tools.common import guarded

ADDITIVE_ANNOTATIONS: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}
OVERWRITE_ANNOTATIONS: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
    "openWorldHint": False,
}
DEFAULT_MIME_TYPE = "application/octet-stream"

ParentId = Annotated[int, Field(ge=1, description="ID of the folder (container) to create it in.")]
NodeName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_NODE_NAME_LENGTH,
        description='Name of the new node. Must not contain / \\ : * ? " < > |',
    ),
]
Description = Annotated[
    str | None,
    Field(max_length=MAX_DESCRIPTION_LENGTH, description="Optional description."),
]


def _checked[T](action: Callable[..., T], *args: object) -> T:
    """Run a validator and turn its error into a tool error."""
    try:
        return action(*args)
    except McpServerError as exc:
        raise ToolError(str(exc)) from exc


def register_write_tools(mcp: FastMCP, service: WriteService, settings: Settings) -> None:
    """Register the write tools on ``mcp``; refuses to do so in read-only mode."""
    if settings.mcp_read_only:
        raise ConfigError("Write tools must not be registered while MCP_READ_ONLY=true.")
    max_upload = settings.mcp_max_upload_bytes

    @mcp.tool(
        name="create_folder",
        annotations=ADDITIVE_ANNOTATIONS,
        description=(
            "Create a new folder inside an existing container. Use it only when the user "
            "asked for a folder. It never overwrites anything: if the name already exists "
            "the call fails. Returns the new node's ID. Not idempotent: do not call it twice "
            "for the same folder."
        ),
    )
    async def create_folder(
        parent_id: ParentId, name: NodeName, description: Description = None
    ) -> CreatedNode:
        parent = _checked(validate_node_id, parent_id, "parent_id")
        return await guarded(
            service.create_folder(
                parent,
                _checked(validate_node_name, name),
                _checked(validate_description, description),
            )
        )

    @mcp.tool(
        name="upload_document",
        annotations=ADDITIVE_ANNOTATIONS,
        description=(
            f"Create a new document in a folder from content you provide: either "
            f"content_text (UTF-8 text) or content_base64 (binary), at most {max_upload} "
            "bytes. Use it only when the user asked to store this content. It creates a new "
            "node and never replaces an existing document: if the name already exists the "
            "call fails. Returns the new node's ID. Not idempotent."
        ),
    )
    async def upload_document(
        parent_id: ParentId,
        name: NodeName,
        content_text: Annotated[
            str | None,
            Field(max_length=max_upload, description="UTF-8 text content. Exclusive with base64."),
        ] = None,
        content_base64: Annotated[
            str | None,
            Field(
                max_length=(max_upload + 2) // 3 * 4 + 16,
                description="Standard base64 of the file. Exclusive with content_text.",
            ),
        ] = None,
        mime_type: Annotated[
            str | None,
            Field(max_length=129, description="Media type such as application/pdf. Optional."),
        ] = None,
        description: Description = None,
    ) -> CreatedNode:
        parent = _checked(validate_node_id, parent_id, "parent_id")
        clean_name = _checked(validate_node_name, name)
        data = _checked(decode_upload, content_text, content_base64, max_upload)
        media_type = (
            _checked(validate_mime_type, mime_type)
            or mimetypes.guess_type(clean_name)[0]
            or DEFAULT_MIME_TYPE
        )
        return await guarded(
            service.upload_document(
                parent,
                clean_name,
                data,
                media_type,
                _checked(validate_description, description),
            )
        )

    @mcp.tool(
        name="update_node_category",
        annotations=OVERWRITE_ANNOTATIONS,
        description=(
            "OVERWRITES attribute values of one category already applied to a node (for "
            "example 'status' or 'customer'). Ask the user to approve the exact change "
            "first, then pass confirm=true. Keys must look like '<category_id>_<attribute_id>' "
            "with plain text values (set rows and multilingual values are not supported). "
            "The previous state is returned for the audit trail. Pass metadata_token (from "
            "OpenText) to detect concurrent edits; a conflict fails without changing data."
        ),
    )
    async def update_node_category(
        node_id: Annotated[int, Field(ge=1, description="Node that carries the category.")],
        category_id: Annotated[int, Field(ge=1, description="ID of the category to update.")],
        values: Annotated[
            dict[str, str],
            Field(
                min_length=1,
                max_length=MAX_CATEGORY_VALUES,
                description="Map of '<category_id>_<attribute_id>' to the new text value.",
            ),
        ],
        confirm: Annotated[
            bool,
            Field(
                strict=True,  # only a JSON boolean counts: the string "true" is not consent
                description="Must be true. Set it only after the user approved this change.",
            ),
        ],
        metadata_token: Annotated[
            str | None,
            Field(max_length=64, description="Optional edit-conflict token from OpenText."),
        ] = None,
    ) -> CategoryUpdateResult:
        _checked(validate_confirmation, confirm)
        node = _checked(validate_node_id, node_id, "node_id")
        category = _checked(validate_node_id, category_id, "category_id")
        cleaned = _checked(validate_category_values, category, values)
        token = _checked(validate_metadata_token, metadata_token)
        return await guarded(service.update_category(node, category, cleaned, token))
