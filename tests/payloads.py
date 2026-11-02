# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Response payload factories shaped after the Content Server / Business Workspaces swagger."""

from typing import Any


def node_props(node_id: int = 2000, **overrides: Any) -> dict[str, Any]:
    props: dict[str, Any] = {
        "id": node_id,
        "name": "Enterprise",
        "type": 141,
        "type_name": "Enterprise Workspace",
        "parent_id": -1,
        "container": True,
        "size": 12,
        "create_date": "2020-01-01T10:00:00",
        "modify_date": "2026-01-01T10:00:00",
        "description": "Root of the repository",
        "owner_user_id": 1000,
        "create_user_id": 1000,
        "modify_user_id": 1001,
        "volume_id": -2000,
        "reserved": False,
        "hidden": False,
        "guid": None,
    }
    props.update(overrides)
    return props


def single(props: dict[str, Any]) -> dict[str, Any]:
    """Single-node response with ``results`` as an object (as the swagger models it)."""
    return {"results": {"data": {"properties": props}}}


def single_as_list(props: dict[str, Any]) -> dict[str, Any]:
    """Single-node response with ``results`` as an array."""
    return {"results": [{"data": {"properties": props}}]}


def listing(
    props_list: list[dict[str, Any]],
    *,
    page: int = 1,
    limit: int = 50,
    total_count: int | None = None,
    page_total: int | None = None,
    paging_at_top: bool = False,
) -> dict[str, Any]:
    paging: dict[str, Any] = {"page": page, "limit": limit}
    if total_count is not None:
        paging["total_count"] = total_count
    if page_total is not None:
        paging["page_total"] = page_total
    body: dict[str, Any] = {"results": [{"data": {"properties": p}} for p in props_list]}
    if paging_at_top:
        body["paging"] = paging
    else:
        body["collection"] = {"paging": paging}
    return body


def versions(number: int = 3, mime_type: str | None = "application/pdf") -> dict[str, Any]:
    info: dict[str, Any] = {"version_number": number, "file_name": "doc.pdf", "file_size": 1234}
    if mime_type is not None:
        info["mime_type"] = mime_type
    return {"results": [{"data": {"versions": info}}]}


def no_versions() -> dict[str, Any]:
    return {"results": [{"data": {"versions": {}}}]}


def categories(entries: list[Any]) -> dict[str, Any]:
    return {"results": [{"data": {"categories": entries}}]}


def workspace_props(workspace_id: int = 5000, **overrides: Any) -> dict[str, Any]:
    props: dict[str, Any] = {
        "id": workspace_id,
        "name": "Customer ACME",
        "description": "Workspace of ACME",
        "parent_id": 4000,
        "create_date": "2025-05-01T09:00:00",
        "modify_date": "2026-02-01T09:00:00",
        "type_name": "Business Workspace",
        "wnf_wksp_type_id": 7,
        "wnf_wksp_type_id_formatted": "Customer",
        "owner_user_id": 1000,
        "create_user_id": 1000,
        "modify_user_id": 1001,
        "rm_enabled": False,
    }
    props.update(overrides)
    return props


def workspace_detail(props: dict[str, Any], references: list[Any] | None = None) -> dict[str, Any]:
    return {
        "results": {
            "data": {
                "properties": props,
                "business_properties": {
                    "workspace_type_id": 7,
                    "workspace_type_name": "Customer",
                },
                "workspace_references": references if references is not None else [],
            }
        }
    }


def members(entries: list[dict[str, Any]], *, page_total: int | None = None) -> dict[str, Any]:
    paging: dict[str, Any] = {"page": 1}
    if page_total is not None:
        paging["page_total"] = page_total
    return {
        "results": [{"data": {"properties": e}} for e in entries],
        "collection": {"paging": paging},
    }


def roles(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"results": [{"data": {"properties": e}} for e in entries]}


def workspace_types(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "results": [{"data": {"properties": e, "wksp_info": {"in_use": True}}} for e in entries]
    }
