# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Records Management read operations (Records Management REST API 26.4).

Only lookups are exposed: holds, RSIs and the RM classification of a node. The v2
endpoints are used where they exist; the node-level endpoints exist only as v1 and
answer with a top-level ``data`` array instead of ``results``.
"""

from typing import Any

from otcs_mcp.client import OtcsClient
from otcs_mcp.config import Settings
from otcs_mcp.errors import OtcsResponseError
from otcs_mcp.models import (
    HoldDetail,
    HoldSummary,
    NodeHold,
    NodeRsi,
    PagedResult,
    RmClassification,
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
    paging_of,
    result_items,
    sanitize_json,
)


def _v1_data(payload: object) -> list[dict[str, Any]]:
    """Return the top-level ``data`` entries used by the v1 Records Management endpoints."""
    data = as_dict(payload).get("data")
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    if isinstance(data, dict):
        return [data]
    raise OtcsResponseError("OpenText returned an unexpected response structure")


def _text(value: object, limit: int = 200) -> str | None:
    return clean_text(value, limit)


def _classification(entry: dict[str, Any]) -> RmClassification:
    return RmClassification(
        id=as_int(entry.get("rmclassification_id")),
        name=_text(entry.get("name"), NAME_LIMIT),
        file_number=_text(entry.get("file_number")),
        rsi=_text(entry.get("rsi")),
        file_status=_text(entry.get("file_status")),
        status_date=_text(entry.get("status_date"), 40),
        record_date=_text(entry.get("record_date"), 40),
        essential=_text(entry.get("essential")),
        official=as_bool(entry.get("official")),
        last_review_date=_text(entry.get("last_review_date"), 40),
        next_review_date=_text(entry.get("next_review_date"), 40),
        accession=_text(entry.get("accession")),
        subject=_text(entry.get("subject"), DESCRIPTION_LIMIT),
        record_officer_name=_text(entry.get("record_officer_name")),
        records_manager_name=_text(entry.get("records_manager_name")),
        inherit_from=as_bool(entry.get("inherit_from")),
        is_primary=as_bool(entry.get("is_primary")),
        management_type=_text(entry.get("management_type"), 50),
    )


def _node_hold(entry: dict[str, Any]) -> NodeHold:
    return NodeHold(
        hold_id=as_int(entry.get("hold_id")),
        hold_name=_text(entry.get("hold_name"), NAME_LIMIT),
        hold_type=_text(entry.get("hold_type"), 50),
        active=as_bool(entry.get("active")),
        applied_by=_text(entry.get("applied_by")),
        date_applied=_text(entry.get("date_applied"), 40),
        hold_comment=_text(entry.get("hold_comment"), DESCRIPTION_LIMIT),
        applied_method=_text(entry.get("hold_appliedmethod"), 50),
        removable=as_bool(entry.get("removable")),
    )


def _hold(entry: dict[str, Any]) -> HoldSummary:
    active = as_int(entry.get("ActiveHold"))
    return HoldSummary(
        id=as_int(entry.get("HoldID")),
        name=_text(entry.get("HoldName"), NAME_LIMIT),
        active=None if active is None else active != 0,
        hold_type=_text(entry.get("HoldType"), 50),
        comment=_text(entry.get("HoldComment"), DESCRIPTION_LIMIT),
        apply_patron=_text(entry.get("ApplyPatron")),
        date_applied=_text(entry.get("DateApplied"), 40),
        date_to_remove=_text(entry.get("DateToRemove"), 40),
        date_removed=_text(entry.get("DateRemoved"), 40),
        parent_id=as_int(entry.get("ParentID")),
    )


def _generic_entries(payload: object) -> list[dict[str, Any]]:
    """Flatten an untyped ``results`` object into bounded entries (RSI listings)."""
    entries: list[dict[str, Any]] = []
    for item in result_items(payload):
        data = item_data(item) or item
        nested = next(
            (
                value
                for value in data.values()
                if isinstance(value, list) and value and all(isinstance(v, dict) for v in value)
            ),
            None,
        )
        entries.extend(nested if nested is not None else [data])
    return [as_dict(sanitize_json(entry)) for entry in entries]


class RecordsService:
    """Read-only Records Management lookups."""

    def __init__(self, client: OtcsClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def node_classifications(
        self, node_id: int, *, limit: int
    ) -> PagedResult[RmClassification]:
        """RM classifications applied to a node (``GET /v1/nodes/{id}/rmclassifications``)."""
        payload = await self._client.get_json(f"/v1/nodes/{node_id}/rmclassifications")
        return build_page(
            _v1_data(payload), _classification, page=1, limit=limit, paging={}, paginated=False
        )

    async def node_holds(self, node_id: int, *, limit: int) -> PagedResult[NodeHold]:
        """Holds applied to a node (``GET /v1/nodes/{id}/holds``)."""
        payload = await self._client.get_json(f"/v1/nodes/{node_id}/holds")
        return build_page(
            _v1_data(payload), _node_hold, page=1, limit=limit, paging={}, paginated=False
        )

    async def node_rsis(self, node_id: int, *, limit: int) -> PagedResult[NodeRsi]:
        """RSIs assigned to a node (``GET /v1/nodes/{id}/rsis``)."""

        def mapper(entry: dict[str, Any]) -> NodeRsi:
            return NodeRsi(class_id=as_int(entry.get("class_id")), rsi=_text(entry.get("rsi")))

        payload = await self._client.get_json(f"/v1/nodes/{node_id}/rsis")
        return build_page(
            _v1_data(payload), mapper, page=1, limit=limit, paging={}, paginated=False
        )

    async def list_holds(self, *, page: int, limit: int) -> PagedResult[HoldSummary]:
        """Holds defined in Records Management (``GET /v2/holds``)."""
        payload = await self._client.get_json("/v2/holds", params={"page": page, "limit": limit})
        raw: list[dict[str, Any]] = []
        for item in result_items(payload):
            holds = item_data(item).get("holds")
            if isinstance(holds, list):
                raw.extend(entry for entry in holds if isinstance(entry, dict))
        return build_page(raw, _hold, page=page, limit=limit, paging=paging_of(payload))

    async def get_hold(self, hold_id: int) -> HoldDetail:
        """One hold by ID (``GET /v2/holds/{id}``)."""
        payload = await self._client.get_json(f"/v2/holds/{hold_id}")
        body = as_dict(payload)
        hold = as_dict(body.get("hold"))
        if not hold:
            for item in result_items(payload):
                hold = as_dict(item_data(item).get("hold")) or as_dict(item.get("hold"))
                if hold:
                    break
        if not hold:
            raise OtcsResponseError("OpenText returned a hold without data")
        return HoldDetail(**_hold(hold).model_dump())

    async def list_rsis(self, *, page: int, limit: int) -> PagedResult[dict[str, Any]]:
        """RSIs with their schedules (``GET /v2/rsis``); the swagger leaves the shape untyped."""
        payload = await self._client.get_json("/v2/rsis", params={"page": page, "limit": limit})
        return build_page(
            _generic_entries(payload),
            lambda entry: entry,
            page=page,
            limit=limit,
            paging=paging_of(payload),
        )
