# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Optional tests against a REAL OpenText Content Management server.

They are excluded from the normal run (``-m 'not integration'``) and are skipped
unless the connection variables are present. They only read data. Run them with:

    OTCS_BASE_URL=... OTCS_USERNAME=... OTCS_PASSWORD=... \\
        python -m pytest tests/integration -m integration --no-cov

They exist mainly to confirm assumptions the swagger does not document: the
accepted ``sort`` values and the behaviour of ``lookfor=allwords`` in search.
"""

import os
from collections.abc import AsyncIterator

import pytest

from otcs_mcp.client import OtcsClient
from otcs_mcp.config import Settings
from otcs_mcp.services.nodes import NodeService
from otcs_mcp.services.workspaces import WorkspaceService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not all(
            os.environ.get(name) for name in ("OTCS_BASE_URL", "OTCS_USERNAME", "OTCS_PASSWORD")
        ),
        reason="OTCS_BASE_URL, OTCS_USERNAME and OTCS_PASSWORD must be set",
    ),
]

TEST_NODE_ID = int(
    os.environ.get("OTCS_TEST_NODE_ID", "2000")
)  # 2000 is usually the Enterprise root


@pytest.fixture
async def live_settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
async def client(live_settings: Settings) -> AsyncIterator[OtcsClient]:
    async with OtcsClient(live_settings) as live:
        yield live


async def test_authentication_and_get_node_work_against_the_real_server(
    client: OtcsClient, live_settings: Settings
) -> None:
    node = await NodeService(client, live_settings).get_node(TEST_NODE_ID)

    assert node.id == TEST_NODE_ID
    assert node.name


@pytest.mark.parametrize(
    "sort", [None, "asc_name", "desc_name", "desc_modify_date", "asc_create_date"]
)
async def test_documented_sort_values_are_accepted_by_the_server(
    client: OtcsClient, live_settings: Settings, sort: str | None
) -> None:
    page = await NodeService(client, live_settings).list_children(
        TEST_NODE_ID, kind="all", name_filter=None, sort=sort, page=1, limit=5
    )

    assert page.page == 1
    assert len(page.items) <= 5


@pytest.mark.parametrize("mode", ["allwords", "anywords", "exactphrase"])
async def test_plain_word_search_is_accepted_in_every_mode(
    client: OtcsClient, live_settings: Settings, mode: str
) -> None:
    page = await NodeService(client, live_settings).search(
        "enterprise", mode=mode, scope="all", page=1, limit=5
    )

    assert page.limit == 5


async def test_business_workspace_endpoints_respond(
    client: OtcsClient, live_settings: Settings
) -> None:
    service = WorkspaceService(client, live_settings)

    types = await service.types(limit=50)
    workspaces = await service.search(name_filter=None, workspace_type_id=None, page=1, limit=5)

    assert types.limit == 50
    assert workspaces.limit == 5
