# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""State-changing operations (Content Server REST API v2).

Registered only when ``MCP_READ_ONLY=false``. Requests follow the swagger: nodes are
created with ``POST /v2/nodes`` whose ``body`` form field holds a JSON string (plus a
``file`` part for documents); categories are updated with
``PUT /v2/nodes/{id}/categories/{category_id}/``.
"""

import json
import logging
from typing import Any

from otcs_mcp.client import OtcsClient
from otcs_mcp.config import Settings
from otcs_mcp.errors import OtcsResponseError, OtcsServerError
from otcs_mcp.models import CategoryUpdateResult, CreatedNode
from otcs_mcp.services.shapes import (
    NAME_LIMIT,
    as_dict,
    as_int,
    clean_text,
    item_data,
    item_properties,
    result_items,
    sanitize_json,
)

logger = logging.getLogger(__name__)

FOLDER_SUBTYPE = 0  # documented default subtype for "Create folder"
DOCUMENT_SUBTYPE = 144  # documented default subtype for "Create document"
MAX_PREVIOUS_ENTRIES = 20

_DUPLICATE_HINT = (
    "OpenText could not create the node (HTTP 500). A node with that name may already exist "
    "in the parent (OpenText reports duplicates this way), or the parent is not a container "
    "the service account may write to."
)


class WriteService:
    """Operations that create or modify repository content."""

    def __init__(self, client: OtcsClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def create_folder(
        self, parent_id: int, name: str, description: str | None
    ) -> CreatedNode:
        """Create a folder under ``parent_id``."""
        body = _node_body(FOLDER_SUBTYPE, parent_id, name, description)
        created = await self._create(body, files=None)
        logger.info(
            "write operation",
            extra={"operation": "create_folder", "target_id": created.id},
        )
        return created

    async def upload_document(
        self,
        parent_id: int,
        name: str,
        content: bytes,
        mime_type: str,
        description: str | None,
    ) -> CreatedNode:
        """Create a new document under ``parent_id`` (never overwrites an existing node)."""
        body = _node_body(DOCUMENT_SUBTYPE, parent_id, name, description)
        created = await self._create(body, files={"file": (name, content, mime_type)})
        logger.info(
            "write operation",
            extra={"operation": "upload_document", "target_id": created.id},
        )
        return created

    async def update_category(
        self,
        node_id: int,
        category_id: int,
        values: dict[str, str],
        metadata_token: str | None,
    ) -> CategoryUpdateResult:
        """Update attributes of one category already applied to a node.

        The category is read first: that proves it is applied to the node (so nothing
        is written otherwise) and records the previous state for the audit trail.
        """
        path = f"/v2/nodes/{node_id}/categories/{category_id}/"
        previous = await self._client.get_json(path)
        entries = [sanitize_json(item_data(item) or item) for item in result_items(previous)]
        form = dict(values)
        if metadata_token:
            form["metadata_token"] = metadata_token
        await self._client.write_json("PUT", path, data=form)
        logger.info(
            "write operation",
            extra={"operation": "update_category", "target_id": node_id},
        )
        return CategoryUpdateResult(
            node_id=node_id,
            category_id=category_id,
            updated_keys=sorted(values),
            previous_state=entries[:MAX_PREVIOUS_ENTRIES],
            previous_state_truncated=len(entries) > MAX_PREVIOUS_ENTRIES,
        )

    async def _create(
        self, body: dict[str, Any], *, files: dict[str, tuple[str, bytes, str]] | None
    ) -> CreatedNode:
        try:
            payload = await self._client.write_json(
                "POST",
                "/v2/nodes",
                data={"body": json.dumps(body, ensure_ascii=False)},
                files=files,
            )
        except OtcsServerError as exc:
            if exc.status_code == 500:
                raise OtcsServerError(_DUPLICATE_HINT, status_code=500) from exc
            raise
        return _created_node(payload, body)


def _node_body(subtype: int, parent_id: int, name: str, description: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"type": subtype, "parent_id": parent_id, "name": name}
    if description:
        body["description"] = description
    return body


def _created_node(payload: Any, body: dict[str, Any]) -> CreatedNode:
    """Read the new node from the reply, tolerating the shapes seen in OpenText docs."""
    items = result_items(payload)
    first = items[0] if items else {}
    props = item_properties(first)
    node_id = as_int(props.get("id"))
    if node_id is None:
        node_id = as_int(first.get("id")) or as_int(as_dict(item_data(first)).get("id"))
    if node_id is None:
        raise OtcsResponseError(
            "OpenText accepted the request but did not return the new node's ID. "
            "Check the repository before retrying so nothing is created twice."
        )
    return CreatedNode(
        id=node_id,
        name=clean_text(props.get("name"), NAME_LIMIT) or str(body["name"]),
        parent_id=as_int(props.get("parent_id")) or int(body["parent_id"]),
        type=as_int(props.get("type")) if props.get("type") is not None else int(body["type"]),
        type_name=clean_text(props.get("type_name"), 100),
    )
