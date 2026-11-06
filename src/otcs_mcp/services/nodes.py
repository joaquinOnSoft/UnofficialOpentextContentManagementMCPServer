# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Node, folder, search, category and document-text operations (Content Server REST API v2)."""

import logging
from typing import Any

from otcs_mcp.client import OtcsClient
from otcs_mcp.config import Settings
from otcs_mcp.errors import (
    OtcsBadRequestError,
    OtcsNotFoundError,
    OtcsResponseError,
    OtcsServerError,
)
from otcs_mcp.models import CategoriesResult, DocumentText, NodeDetail, NodeSummary, PagedResult
from otcs_mcp.security import wrap_untrusted
from otcs_mcp.services.shapes import (
    DESCRIPTION_LIMIT,
    NAME_LIMIT,
    SUMMARY_LIMIT,
    as_bool,
    as_dict,
    as_int,
    build_page,
    clean_text,
    item_data,
    item_properties,
    paging_of,
    result_items,
    sanitize_json,
)

logger = logging.getLogger(__name__)

# Documented in the swagger: where_type -1 = all containers, -3 = all non-containers.
KIND_TO_WHERE_TYPE = {"all": None, "containers": "-1", "non_containers": "-3"}
SORT_FIELDS = ("name", "modify_date", "create_date")
SEARCH_MODES = ("allwords", "anywords", "exactphrase")  # never "complexquery" (LQL)
SEARCH_SCOPES = ("all", "content", "metadata")
MAX_CATEGORY_ENTRIES = 200
BINARY_SNIFF_BYTES = 8192
_TEXT_MIME_TYPES = frozenset({"application/json", "application/xml", "application/xhtml+xml"})


def _is_text_mime(mime_type: str | None) -> bool:
    if not mime_type:
        return False
    base = mime_type.split(";")[0].strip().lower()
    return base.startswith("text/") or base in _TEXT_MIME_TYPES or base.endswith(("+json", "+xml"))


def _summary_from_properties(props: dict[str, Any]) -> NodeSummary | None:
    node_id = as_int(props.get("id"))
    if node_id is None:
        logger.debug("Skipping a result without a numeric id")
        return None
    return NodeSummary(
        id=node_id,
        name=clean_text(props.get("name"), NAME_LIMIT) or "",
        type=as_int(props.get("type")),
        type_name=clean_text(props.get("type_name"), 100),
        parent_id=as_int(props.get("parent_id")),
        container=as_bool(props.get("container")),
        size=as_int(props.get("size")),
        mime_type=clean_text(props.get("mime_type"), 100),
        create_date=clean_text(props.get("create_date"), 40),
        modify_date=clean_text(props.get("modify_date"), 40),
        description=clean_text(props.get("description"), DESCRIPTION_LIMIT),
        summary=clean_text(props.get("summary") or props.get("short_summary"), SUMMARY_LIMIT),
    )


def _summary_from_item(item: dict[str, Any]) -> NodeSummary | None:
    return _summary_from_properties(item_properties(item))


class NodeService:
    """Read-only operations on repository nodes."""

    def __init__(self, client: OtcsClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def get_node(self, node_id: int) -> NodeDetail:
        """Return the properties of one node."""
        payload = await self._client.get_json(
            f"/v2/nodes/{node_id}", params={"fields": "properties"}
        )
        items = result_items(payload)
        props = item_properties(items[0]) if items else {}
        summary = _summary_from_properties(props)
        if summary is None:
            raise OtcsResponseError("OpenText returned a node without an id")
        return NodeDetail(
            **summary.model_dump(),
            owner_user_id=as_int(props.get("owner_user_id")),
            create_user_id=as_int(props.get("create_user_id")),
            modify_user_id=as_int(props.get("modify_user_id")),
            volume_id=as_int(props.get("volume_id")),
            reserved=as_bool(props.get("reserved")),
            hidden=as_bool(props.get("hidden")),
            guid=clean_text(props.get("guid"), 64),
        )

    async def list_children(
        self,
        node_id: int,
        *,
        kind: str,
        name_filter: str | None,
        sort: str | None,
        page: int,
        limit: int,
    ) -> PagedResult[NodeSummary]:
        """List the direct children of a container node."""
        where_type = KIND_TO_WHERE_TYPE[kind]
        payload = await self._client.get_json(
            f"/v2/nodes/{node_id}/nodes",
            params={
                "fields": "properties",
                "page": page,
                "limit": limit,
                "sort": sort,
                "where_name": name_filter,
                "where_type": [where_type] if where_type else None,
            },
        )
        return build_page(
            result_items(payload),
            _summary_from_item,
            page=page,
            limit=limit,
            paging=paging_of(payload),
        )

    async def search(
        self, query: str, *, mode: str, scope: str, page: int, limit: int
    ) -> PagedResult[NodeSummary]:
        """Run a plain-words full-text/metadata search (never complex-query mode)."""
        payload = await self._client.post_form_json(
            "/v2/search",
            data={
                "where": query,
                "lookfor": mode,
                "within": scope,
                "page": str(page),
                "limit": str(limit),
            },
        )
        return build_page(
            result_items(payload),
            _summary_from_item,
            page=page,
            limit=limit,
            paging=paging_of(payload),
        )

    async def get_categories(self, node_id: int) -> CategoriesResult:
        """Return the category values of a node (bounded)."""
        payload = await self._client.get_json(f"/v2/nodes/{node_id}/categories")
        entries: list[dict[str, Any]] = []
        for item in result_items(payload):
            data = item_data(item)
            categories = data.get("categories")
            if isinstance(categories, list):
                entries.extend(entry for entry in categories if isinstance(entry, dict))
            elif isinstance(categories, dict):
                entries.append(categories)
            elif data:
                entries.append(data)
        truncated = len(entries) > MAX_CATEGORY_ENTRIES
        return CategoriesResult(
            node_id=node_id,
            categories=[sanitize_json(entry) for entry in entries[:MAX_CATEGORY_ENTRIES]],
            truncated=truncated,
        )

    async def read_text(self, node_id: int, *, max_bytes: int) -> DocumentText:
        """Return the text of the latest version, or explain why none is available."""
        version = await self._latest_version(node_id)
        number = as_int(version.get("version_number")) if version else None
        if version is None or number is None:
            return DocumentText(
                node_id=node_id,
                available=False,
                reason="The node has no content versions (it is probably not a document).",
            )
        mime_type = clean_text(version.get("mime_type"), 100)
        base = f"/v2/nodes/{node_id}/versions/{number}"
        source = "plain_rendition"
        try:
            data, truncated = await self._client.get_bytes(
                f"{base}/view/plain", max_bytes=max_bytes
            )
        except (OtcsNotFoundError, OtcsBadRequestError, OtcsServerError):
            if not _is_text_mime(mime_type):
                return DocumentText(
                    node_id=node_id,
                    available=False,
                    version_number=number,
                    mime_type=mime_type,
                    reason=(
                        "No plain-text rendition is available and the content type "
                        f"({mime_type or 'unknown'}) is not text; only metadata is returned."
                    ),
                )
            source = "raw_content"
            data, truncated = await self._client.get_bytes(f"{base}/content", max_bytes=max_bytes)
            if b"\x00" in data[:BINARY_SNIFF_BYTES]:
                return DocumentText(
                    node_id=node_id,
                    available=False,
                    version_number=number,
                    mime_type=mime_type,
                    reason="The content looks binary; it is not returned.",
                )
        text = data.decode("utf-8", errors="ignore" if truncated else "replace")
        return DocumentText(
            node_id=node_id,
            available=True,
            source=source,
            version_number=number,
            mime_type=mime_type,
            text=wrap_untrusted(text, source="otcs", node_id=node_id),
            truncated=truncated,
            bytes_returned=len(data),
        )

    async def _latest_version(self, node_id: int) -> dict[str, Any] | None:
        payload = await self._client.get_json(
            f"/v2/nodes/{node_id}/versions", params={"limit": 1, "order": "desc"}
        )
        for item in result_items(payload):
            versions = item_data(item).get("versions")
            if isinstance(versions, list):
                versions = versions[0] if versions else None
            if isinstance(versions, dict) and versions:
                return as_dict(versions)
        return None
