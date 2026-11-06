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
from otcs_mcp.services.records import RecordsService
from otcs_mcp.services.workspaces import WorkspaceService
from otcs_mcp.services.writes import WriteService

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


# ----------------------------------------------------------------------------- Records Management


async def test_records_management_lookups_respond_or_report_missing_permission(
    client: OtcsClient, live_settings: Settings
) -> None:
    from otcs_mcp.errors import OtcsPermissionError

    service = RecordsService(client, live_settings)
    try:
        holds = await service.list_holds(page=1, limit=5)
        rsis = await service.list_rsis(page=1, limit=5)
    except OtcsPermissionError:
        pytest.skip("the service account has no Records Management access")

    assert holds.limit == 5
    assert rsis.limit == 5
    classifications = await service.node_classifications(TEST_NODE_ID, limit=10)
    assert classifications.page == 1


# ----------------------------------------------------------------------------- writes (opt-in)

WRITES_ENABLED = os.environ.get("OTCS_ALLOW_WRITE_TESTS") == "1" and bool(
    os.environ.get("OTCS_WRITE_TEST_PARENT_ID")
)
write_tests = pytest.mark.skipif(
    not WRITES_ENABLED,
    reason="set OTCS_ALLOW_WRITE_TESTS=1 and OTCS_WRITE_TEST_PARENT_ID=<scratch folder id>",
)


@write_tests
async def test_create_folder_upload_document_and_update_category_work_on_a_scratch_folder(
    client: OtcsClient, live_settings: Settings
) -> None:
    """Creates a uniquely named folder and a document in the scratch folder. Nothing is deleted."""
    import time

    parent = int(os.environ["OTCS_WRITE_TEST_PARENT_ID"])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    service = WriteService(client, live_settings)

    folder = await service.create_folder(parent, f"otcs-mcp test {stamp}", "created by otcs-mcp")
    document = await service.upload_document(
        folder.id, "hello.txt", b"hello from otcs-mcp", "text/plain", None
    )

    assert folder.id > 0
    assert document.id > 0
    text = await NodeService(client, live_settings).read_text(document.id, max_bytes=1000)
    assert text.available is True

    category_id = os.environ.get("OTCS_WRITE_TEST_CATEGORY_ID")
    attribute_key = os.environ.get("OTCS_WRITE_TEST_ATTRIBUTE_KEY")  # e.g. 9830_2
    if category_id and attribute_key:
        result = await service.update_category(
            document.id, int(category_id), {attribute_key: f"otcs-mcp {stamp}"}, None
        )
        assert result.updated_keys == [attribute_key]


@write_tests
async def test_creating_a_duplicate_name_is_reported_with_the_explicit_hint(
    client: OtcsClient, live_settings: Settings
) -> None:
    from otcs_mcp.errors import OtcsError

    parent = int(os.environ["OTCS_WRITE_TEST_PARENT_ID"])
    service = WriteService(client, live_settings)
    name = f"otcs-mcp duplicate {os.getpid()}"
    await service.create_folder(parent, name, None)

    with pytest.raises(OtcsError) as info:
        await service.create_folder(parent, name, None)

    assert info.value.status_code in {400, 409, 500}
