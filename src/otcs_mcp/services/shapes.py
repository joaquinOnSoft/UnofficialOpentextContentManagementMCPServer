# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Tolerant helpers for reading OpenText payloads.

The Content Server swagger models list and single results inconsistently (a
``results`` object in some schemas, an array in others), so these helpers
accept both shapes and fail with a clear error when neither fits.
"""

import logging
import unicodedata
from collections.abc import Callable
from typing import Any

from otcs_mcp.errors import OtcsResponseError
from otcs_mcp.models import PagedResult

logger = logging.getLogger(__name__)

DESCRIPTION_LIMIT = 500
SUMMARY_LIMIT = 300
NAME_LIMIT = 300


def clean_text(value: object, limit: int = DESCRIPTION_LIMIT) -> str | None:
    """Return printable text clipped to ``limit`` characters, or ``None`` if empty."""
    if value is None:
        return None
    text = "".join(
        char if unicodedata.category(char) not in {"Cc", "Cs"} else " " for char in str(value)
    ).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def as_int(value: object) -> int | None:
    """Return ``value`` if it is a real integer (not a bool), else ``None``."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def as_bool(value: object) -> bool | None:
    """Return ``value`` if it is a bool, else ``None``."""
    return value if isinstance(value, bool) else None


def as_dict(value: object) -> dict[str, Any]:
    """Return ``value`` if it is a dict, else an empty dict."""
    return value if isinstance(value, dict) else {}


def result_items(payload: object) -> list[dict[str, Any]]:
    """Return the entries of ``payload['results']`` whether it is a list or an object."""
    results = as_dict(payload).get("results")
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]
    if isinstance(results, dict):
        return [results]
    raise OtcsResponseError("OpenText returned an unexpected response structure")


def item_data(item: dict[str, Any]) -> dict[str, Any]:
    """Return the ``data`` section of a result entry."""
    return as_dict(item.get("data"))


def item_properties(item: dict[str, Any]) -> dict[str, Any]:
    """Return ``data.properties`` of a result entry."""
    return as_dict(item_data(item).get("properties"))


def paging_of(payload: object) -> dict[str, Any]:
    """Return the paging object, wherever this endpoint places it."""
    body = as_dict(payload)
    collection = as_dict(body.get("collection"))
    return as_dict(collection.get("paging")) or as_dict(body.get("paging"))


def build_page[T, M](
    raw_items: list[M],
    mapper: Callable[[M], T | None],
    *,
    page: int,
    limit: int,
    paging: dict[str, Any],
    paginated: bool = True,
) -> PagedResult[T]:
    """Map raw entries, apply the limit and compute truncation metadata."""
    mapped = [value for value in (mapper(item) for item in raw_items) if value is not None]
    sliced = len(mapped) > limit
    items = mapped[:limit]
    total_count = as_int(paging.get("total_count"))
    page_total = as_int(paging.get("page_total"))
    if page_total is not None:
        has_more = page < page_total
    elif total_count is not None:
        has_more = page * limit < total_count
    else:
        has_more = paginated and len(raw_items) >= limit
    hint = f"More results exist: call again with page={page + 1}." if has_more else None
    if sliced and not has_more:
        # OpenText ignored the limit: continuing with page+1 would skip entries.
        hint = "The server returned more items than requested; narrow the query to see the rest."
    return PagedResult[T](
        items=items,
        page=page,
        limit=limit,
        total_count=total_count,
        has_more=has_more,
        next_page=page + 1 if has_more else None,
        truncated=has_more or sliced,
        hint=hint,
    )
