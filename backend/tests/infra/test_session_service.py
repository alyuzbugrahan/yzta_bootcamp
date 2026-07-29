"""Session lifecycle through the service layer — the desktop SessionManager's job."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.models import Detection, InspectionResult
from app.services.session_service import build_batch_id


def result(decision: str = "Healthy"):
    return InspectionResult(
        decision=decision,
        confidence=0.9,
        detection=Detection(decision, 0.9, (0.2, 0.2, 0.6, 0.6)),
        latency_ms=50.0,
    )


def test_batch_id_format_matches_the_desktop():
    moment = datetime(2026, 7, 29, 10, 15, 0, tzinfo=UTC)

    assert build_batch_id(moment) == "BATCH_20260729_101500"


async def test_full_lifecycle(service, farmer, db):
    scan = await service.start(farmer.id, conf_threshold=0.55, device_label="Barn cam")

    for decision in ("Healthy", "Aflatoxin", "Healthy", "Healthy"):
        await service.record(scan, result(decision))
    await db.commit()

    closed = await service.stop(farmer.id, scan.uuid)
    summary = await service.summary(farmer.id, scan.uuid)

    assert closed.total_count == 4
    assert closed.defect_count == 1
    assert summary.ratio_pct == 25.0
    assert summary.healthy == 3


async def test_device_label_is_persisted(service, farmer):
    """Recorded so a misclassification can be traced back to the camera that produced it."""
    scan = await service.start(farmer.id, 0.5, device_label="Greenhouse UV rig")

    found = await service.get(farmer.id, scan.uuid)

    assert found.device_label == "Greenhouse UV rig"


async def test_recording_assigns_sequence_numbers(service, farmer, db):
    scan = await service.start(farmer.id, 0.5)

    recorded = [await service.record(scan, result()) for _ in range(3)]
    await db.commit()

    assert [r.fig_seq for r in recorded] == [1, 2, 3]


async def test_history_is_per_farmer(service, farmer, other_farmer, db):
    await service.start(farmer.id, 0.5)
    await service.start(other_farmer.id, 0.5)
    await db.commit()

    assert len(await service.history(farmer.id)) == 1


async def test_stopping_another_farmers_session_fails(service, farmer, other_farmer):
    scan = await service.start(farmer.id, 0.5)

    assert await service.stop(other_farmer.id, scan.uuid) is None


async def test_inspection_listing_is_scoped_to_the_session(service, farmer, db):
    first = await service.start(farmer.id, 0.5)
    second = await service.start(farmer.id, 0.5)

    await service.record(first, result())
    await service.record(first, result())
    await service.record(second, result())
    await db.commit()

    assert len(await service.inspections(first)) == 2
    assert len(await service.inspections(second)) == 1


async def test_delete_removes_the_session(service, farmer, db):
    scan = await service.start(farmer.id, 0.5)
    await service.record(scan, result())
    await db.commit()

    assert await service.delete(farmer.id, scan.uuid) is True
    await db.commit()

    assert await service.get(farmer.id, scan.uuid) is None
