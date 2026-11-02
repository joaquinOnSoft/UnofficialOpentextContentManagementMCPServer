# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Defence in depth: the tool-level validators must hold even if the schema layer is bypassed."""

import pytest
from fastmcp.exceptions import ToolError

from otcs_mcp.config import Settings
from otcs_mcp.tools import common
from otcs_mcp.tools import nodes as node_tools
from otcs_mcp.tools import workspaces as workspace_tools


@pytest.mark.parametrize("value", [0, -1, True, "7", None, 2**63])
def test_node_id_validator_raises_tool_error_when_value_is_not_a_positive_integer(
    value: object,
) -> None:
    with pytest.raises(ToolError, match="node_id"):
        node_tools._node_id(value)


@pytest.mark.parametrize("value", [0, -1, True, "7", None])
def test_workspace_id_validator_raises_tool_error_when_value_is_not_a_positive_integer(
    value: object,
) -> None:
    with pytest.raises(ToolError, match="workspace_id"):
        workspace_tools._id(value, "workspace_id")


@pytest.mark.parametrize("query", ['a"b', "x(y)", "", None, 5])
def test_search_text_validator_raises_tool_error_when_query_is_unsafe(query: object) -> None:
    with pytest.raises(ToolError):
        node_tools._search_text(query)


@pytest.mark.parametrize(("page", "limit"), [(0, 5), (1, 0), (True, 5), ("1", 5)])
def test_page_arguments_raise_tool_error_when_values_are_invalid(
    settings: Settings, page: object, limit: object
) -> None:
    with pytest.raises(ToolError):
        common.page_arguments(settings, page, limit)


def test_page_arguments_clamp_to_server_maximum(settings: Settings) -> None:
    assert common.page_arguments(settings, None, 10_000) == (1, settings.mcp_max_response_items)


@pytest.mark.parametrize("value", ["a\x00b", "x" * 201, "", 5])
def test_optional_text_raises_tool_error_when_value_is_unsafe(value: object) -> None:
    with pytest.raises(ToolError):
        common.optional_text(value, "name_filter")


def test_optional_text_passes_none_through_and_strips_text() -> None:
    assert common.optional_text(None, "name_filter") is None
    assert common.optional_text("  abc ", "name_filter") == "abc"
