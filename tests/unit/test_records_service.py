# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

import httpx
import pytest

from otcs_mcp.config import Settings
from otcs_mcp.errors import OtcsPermissionError, OtcsResponseError
from otcs_mcp.services.records import RecordsService
from tests import payloads
from tests.conftest import Backend, ClientFactory


@pytest.fixture
def service(make_client: ClientFactory, settings: Settings) -> RecordsService:
    return RecordsService(make_client(), settings)


def _json(payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload)


async def test_node_classifications_map_the_documented_fields_and_drop_internal_ones(
    service: RecordsService, backend: Backend
) -> None:
    backend.script = [_json(payloads.rm_classifications([payloads.rm_classification_entry()]))]

    result = await service.node_classifications(123, limit=50)

    assert backend.data_requests[0].url.path.endswith("/v1/nodes/123/rmclassifications")
    item = result.items[0]
    assert (item.id, item.name, item.file_number, item.rsi) == (
        77,
        "Contracts 2026",
        "C-2026-001",
        "CONTRACT-7Y",
    )
    assert (item.official, item.is_primary, item.inherit_from) == (True, True, False)
    assert item.next_review_date == "2027-02-01"
    assert item.record_officer_name == "Ana Lopez"
    assert "extradata" not in item.model_dump()
    assert (result.has_more, result.truncated) == (False, False)


async def test_node_holds_map_the_documented_fields(
    service: RecordsService, backend: Backend
) -> None:
    backend.script = [_json(payloads.node_holds([payloads.node_hold_entry()]))]

    result = await service.node_holds(123, limit=50)

    assert backend.data_requests[0].url.path.endswith("/v1/nodes/123/holds")
    hold = result.items[0]
    assert (hold.hold_id, hold.hold_name, hold.active, hold.applied_method) == (
        5,
        "Litigation 2026",
        True,
        "Manual",
    )
    assert hold.hold_comment == "Do not dispose"


async def test_node_rsis_map_class_and_rsi(service: RecordsService, backend: Backend) -> None:
    backend.script = [_json({"data": [{"class_id": 77, "rsi": "CONTRACT-7Y"}]})]

    result = await service.node_rsis(123, limit=50)

    assert backend.data_requests[0].url.path.endswith("/v1/nodes/123/rsis")
    assert [(r.class_id, r.rsi) for r in result.items] == [(77, "CONTRACT-7Y")]


@pytest.mark.parametrize(
    "payload",
    [{"data": {"rsi": "X", "class_id": 1}}, {"data": [{"rsi": "X", "class_id": 1}, "junk"]}],
)
async def test_v1_data_accepts_an_object_or_a_list_and_drops_junk(
    service: RecordsService, backend: Backend, payload: dict[str, object]
) -> None:
    backend.script = [_json(payload)]

    assert [r.rsi for r in (await service.node_rsis(1, limit=10)).items] == ["X"]


@pytest.mark.parametrize("payload", [{}, {"data": "x"}, {"data": None}, [], {"results": []}])
async def test_v1_data_raises_when_the_structure_is_unexpected(
    service: RecordsService, backend: Backend, payload: object
) -> None:
    backend.script = [_json(payload)]

    with pytest.raises(OtcsResponseError, match="unexpected response structure"):
        await service.node_holds(1, limit=10)


async def test_node_lists_are_cut_to_the_limit_and_flagged_without_a_next_page(
    service: RecordsService, backend: Backend
) -> None:
    entries = [payloads.node_hold_entry(hold_id=i) for i in range(5)]
    backend.script = [_json(payloads.node_holds(entries))]

    result = await service.node_holds(1, limit=3)

    assert [h.hold_id for h in result.items] == [0, 1, 2]
    assert result.truncated is True
    assert result.has_more is False
    assert result.next_page is None


async def test_list_holds_sends_required_paging_and_maps_pascal_case_fields(
    service: RecordsService, backend: Backend
) -> None:
    backend.script = [
        _json(
            payloads.holds_list(
                [payloads.hold_entry(), payloads.hold_entry(HoldID=6, ActiveHold=0)], page_total=3
            )
        )
    ]

    result = await service.list_holds(page=2, limit=2)

    request = backend.data_requests[0]
    assert request.url.path.endswith("/v2/holds")
    assert (request.url.params["page"], request.url.params["limit"]) == ("2", "2")
    assert [(h.id, h.active) for h in result.items] == [(5, True), (6, False)]
    assert result.items[0].apply_patron == "ana"
    assert (result.has_more, result.next_page) == (True, 3)


async def test_list_holds_handles_an_empty_list_and_entries_without_active_flag(
    service: RecordsService, backend: Backend
) -> None:
    no_flag = payloads.hold_entry()
    del no_flag["ActiveHold"]
    backend.script = [_json(payloads.holds_list([no_flag], page_total=1))]

    result = await service.list_holds(page=1, limit=10)

    assert result.items[0].active is None
    backend.script = [_json({"results": {"data": {}}})]
    assert (await service.list_holds(page=1, limit=10)).items == []


@pytest.mark.parametrize(
    "reply",
    [
        {"hold": payloads.hold_entry()},
        {"results": {"data": {"hold": payloads.hold_entry()}}},
        {"results": [{"hold": payloads.hold_entry()}]},
    ],
)
async def test_get_hold_reads_every_documented_reply_shape(
    service: RecordsService, backend: Backend, reply: dict[str, object]
) -> None:
    backend.script = [_json(reply)]

    hold = await service.get_hold(5)

    assert backend.data_requests[0].url.path.endswith("/v2/holds/5")
    assert (hold.id, hold.name, hold.hold_type) == (5, "Litigation 2026", "Legal")
    assert "untrusted" in hold.notice


async def test_get_hold_raises_when_the_reply_has_no_hold(
    service: RecordsService, backend: Backend
) -> None:
    backend.script = [_json({"results": {"data": {}}})]

    with pytest.raises(OtcsResponseError, match="without data"):
        await service.get_hold(5)


async def test_list_rsis_flattens_untyped_results_and_sanitises_text(
    service: RecordsService, backend: Backend
) -> None:
    reply = {
        "collection": {"paging": {"page": 1, "page_total": 2, "total_count": 4}},
        "results": {
            "data": {"rsis": [{"RSI": "A\x00B", "Schedules": [{"Stage": 1}]}, {"RSI": "C"}]}
        },
    }
    backend.script = [_json(reply)]

    result = await service.list_rsis(page=1, limit=2)

    assert backend.data_requests[0].url.path.endswith("/v2/rsis")
    assert result.items == [{"RSI": "A B", "Schedules": [{"Stage": 1}]}, {"RSI": "C"}]
    assert (result.has_more, result.next_page, result.total_count) == (True, 2, 4)


@pytest.mark.parametrize("reply", [{"results": {"RSI": "A"}}, {"results": [{"RSI": "A"}]}])
async def test_list_rsis_accepts_a_flat_object_or_list_of_entries(
    service: RecordsService, backend: Backend, reply: dict[str, object]
) -> None:
    backend.script = [_json(reply)]

    assert (await service.list_rsis(page=1, limit=5)).items == [{"RSI": "A"}]


async def test_records_permission_errors_surface_without_retries(
    make_client: ClientFactory, settings: Settings, backend: Backend, sleeps: list[float]
) -> None:
    service = RecordsService(make_client(), settings)
    backend.script = [httpx.Response(403)]

    with pytest.raises(OtcsPermissionError):
        await service.list_holds(page=1, limit=10)

    assert sleeps == []
