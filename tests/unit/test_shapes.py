# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

from typing import Any

import pytest

from otcs_mcp.errors import OtcsResponseError
from otcs_mcp.services import shapes


def _identity(value: int) -> int | None:
    return value


# ----------------------------------------------------------------- clean_text


@pytest.mark.parametrize("value", [None, "", "   ", "\x00\x01", "\n\t"])
def test_clean_text_returns_none_when_nothing_printable(value: object) -> None:
    assert shapes.clean_text(value) is None


def test_clean_text_replaces_control_characters_with_spaces() -> None:
    assert shapes.clean_text("a\x00b\nc\x1bd") == "a b c d"


def test_clean_text_clips_long_values_with_an_ellipsis() -> None:
    result = shapes.clean_text("x" * 50, limit=10)

    assert result == "x" * 9 + "…"
    assert result is not None
    assert len(result) == 10


def test_clean_text_keeps_value_when_exactly_at_limit() -> None:
    assert shapes.clean_text("x" * 10, limit=10) == "x" * 10


def test_clean_text_converts_non_strings() -> None:
    assert shapes.clean_text(1234) == "1234"


# ----------------------------------------------------------------- scalar coercion


@pytest.mark.parametrize(
    ("value", "expected"), [(5, 5), (0, 0), (True, None), ("5", None), (None, None), (1.5, None)]
)
def test_as_int_accepts_only_real_integers(value: object, expected: int | None) -> None:
    assert shapes.as_int(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [(True, True), (False, False), (1, None), ("true", None), (None, None)]
)
def test_as_bool_accepts_only_real_booleans(value: object, expected: bool | None) -> None:
    assert shapes.as_bool(value) is expected


def test_as_dict_returns_empty_dict_when_value_is_not_a_dict() -> None:
    assert shapes.as_dict([1]) == {}
    assert shapes.as_dict({"a": 1}) == {"a": 1}


# ----------------------------------------------------------------- result_items


def test_result_items_accepts_a_list_and_drops_non_dict_entries() -> None:
    assert shapes.result_items({"results": [{"a": 1}, "junk", 3]}) == [{"a": 1}]


def test_result_items_wraps_a_single_object() -> None:
    assert shapes.result_items({"results": {"a": 1}}) == [{"a": 1}]


@pytest.mark.parametrize("payload", [None, [], "text", {}, {"results": "x"}, {"results": None}])
def test_result_items_raises_when_structure_is_unexpected(payload: object) -> None:
    with pytest.raises(OtcsResponseError, match="unexpected response structure"):
        shapes.result_items(payload)


def test_item_properties_returns_empty_dict_when_section_is_missing() -> None:
    assert shapes.item_properties({"data": {}}) == {}
    assert shapes.item_properties({}) == {}
    assert shapes.item_properties({"data": {"properties": {"id": 1}}}) == {"id": 1}


# ----------------------------------------------------------------- paging_of


def test_paging_of_reads_collection_paging() -> None:
    assert shapes.paging_of({"collection": {"paging": {"page": 2}}}) == {"page": 2}


def test_paging_of_reads_top_level_paging_when_collection_is_absent() -> None:
    assert shapes.paging_of({"paging": {"page": 3}}) == {"page": 3}


@pytest.mark.parametrize("payload", [{}, None, {"collection": []}])
def test_paging_of_returns_empty_when_no_paging(payload: object) -> None:
    assert shapes.paging_of(payload) == {}


# ----------------------------------------------------------------- build_page


def test_build_page_reports_more_when_page_total_is_greater_than_page() -> None:
    page = shapes.build_page(
        [1, 2], _identity, page=1, limit=2, paging={"page_total": 3, "total_count": 6}
    )

    assert page.items == [1, 2]
    assert page.has_more is True
    assert page.next_page == 2
    assert page.truncated is True
    assert page.total_count == 6
    assert page.hint == "More results exist: call again with page=2."


def test_build_page_reports_no_more_on_the_last_page() -> None:
    page = shapes.build_page([5], _identity, page=3, limit=2, paging={"page_total": 3})

    assert (page.has_more, page.next_page, page.truncated, page.hint) == (False, None, False, None)


def test_build_page_uses_total_count_when_page_total_is_missing() -> None:
    more = shapes.build_page([1, 2], _identity, page=1, limit=2, paging={"total_count": 5})
    done = shapes.build_page([1, 2], _identity, page=3, limit=2, paging={"total_count": 6})

    assert more.has_more is True
    assert done.has_more is False


def test_build_page_guesses_from_item_count_when_server_gives_no_totals() -> None:
    full = shapes.build_page([1, 2], _identity, page=1, limit=2, paging={})
    partial = shapes.build_page([1], _identity, page=1, limit=2, paging={})

    assert full.has_more is True
    assert partial.has_more is False


def test_build_page_never_claims_more_when_endpoint_is_not_paginated() -> None:
    page = shapes.build_page([1, 2], _identity, page=1, limit=2, paging={}, paginated=False)

    assert page.has_more is False
    assert page.truncated is False


def test_build_page_cuts_items_and_tells_to_narrow_when_server_ignores_limit() -> None:
    page = shapes.build_page([1, 2, 3, 4], _identity, page=1, limit=2, paging={}, paginated=False)

    assert page.items == [1, 2]
    assert page.truncated is True
    assert page.has_more is False
    assert page.next_page is None
    assert page.hint is not None
    assert "narrow" in page.hint


def test_build_page_skips_entries_the_mapper_rejects() -> None:
    def mapper(value: int) -> int | None:
        return None if value % 2 else value

    page = shapes.build_page([1, 2, 3, 4], mapper, page=1, limit=10, paging={})

    assert page.items == [2, 4]


def test_build_page_handles_an_empty_result_list() -> None:
    raw: list[Any] = []

    page = shapes.build_page(raw, _identity, page=1, limit=5, paging={})

    assert page.items == []
    assert page.has_more is False
