"""Session persistence, ownership isolation, and the two SQL fixes from the plan."""

from __future__ import annotations

import uuid as uuid_module

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain.models import Detection, InspectionResult
from app.infra.db.models import ScanSession


def result(decision: str = "Healthy", confidence: float = 0.9, latency: float = 50.0):
    return InspectionResult(
        decision=decision,
        confidence=confidence,
        detection=Detection(decision, confidence, (0.2, 0.2, 0.6, 0.6)),
        latency_ms=latency,
    )


async def test_create_and_fetch(sessions, farmer):
    created = await sessions.create(farmer.id, "BATCH_20260729_101500", 0.5)

    found = await sessions.get(farmer.id, created.uuid)

    assert found is not None
    assert found.batch_id == "BATCH_20260729_101500"
    assert found.is_open


async def test_same_batch_id_for_two_farmers_is_allowed(sessions, farmer, other_farmer):
    """The collision the desktop schema would have caused.

    ``batch_id`` came from a second-resolution timestamp under a global UNIQUE constraint, so
    two farmers pressing start in the same second would have hit an IntegrityError. Scoping
    uniqueness per user is what makes concurrent use possible at all.
    """
    batch_id = "BATCH_20260729_101500"

    a = await sessions.create(farmer.id, batch_id, 0.5)
    b = await sessions.create(other_farmer.id, batch_id, 0.5)

    assert a.batch_id == b.batch_id
    assert a.uuid != b.uuid


async def test_same_farmer_restarting_within_a_second_gets_a_suffix(sessions, farmer):
    batch_id = "BATCH_20260729_101500"

    first = await sessions.create(farmer.id, batch_id, 0.5)
    second = await sessions.create(farmer.id, batch_id, 0.5)

    assert first.batch_id == batch_id
    assert second.batch_id == f"{batch_id}_2"


async def test_batch_collision_does_not_discard_earlier_work(sessions, inspections, farmer):
    """A retried batch_id must cost only the failed INSERT.

    The collision path rolls back to a SAVEPOINT. An earlier version called
    ``session.rollback()``, which unwound the caller's entire transaction — so starting a
    second session in the same second silently destroyed the first session and every
    inspection already recorded against it.
    """
    first = await sessions.create(farmer.id, "BATCH_SAME_SECOND", 0.5)
    await inspections.record(first.id, result())
    await inspections.record(first.id, result())

    second = await sessions.create(farmer.id, "BATCH_SAME_SECOND", 0.5)

    assert second.batch_id == "BATCH_SAME_SECOND_2"
    assert await sessions.get(farmer.id, first.uuid) is not None, "first session was lost"
    assert await inspections.count_for_session(first.id) == 2, "inspections were lost"


async def test_another_farmer_cannot_read_the_session(sessions, farmer, other_farmer):
    created = await sessions.create(farmer.id, "BATCH_A", 0.5)

    assert await sessions.get(other_farmer.id, created.uuid) is None


async def test_unknown_uuid_returns_none(sessions, farmer):
    assert await sessions.get(farmer.id, uuid_module.uuid4()) is None


async def test_summary_counts_by_decision(sessions, inspections, farmer, db):
    """Exercises ``COUNT(*) FILTER``, which replaced the SQLite-only boolean SUM."""
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)
    for decision in ("Healthy", "Aflatoxin", "Healthy", "Aflatoxin", "Aflatoxin"):
        await inspections.record(scan.id, result(decision, 0.8, 40.0))
    await db.commit()

    summary = await sessions.summary(farmer.id, scan.uuid)

    assert summary.total == 5
    assert summary.aflatoxin == 3
    assert summary.healthy == 2
    assert summary.ratio_pct == 60.0


async def test_summary_of_empty_session_does_not_divide_by_zero(sessions, farmer):
    scan = await sessions.create(farmer.id, "BATCH_EMPTY", 0.5)

    summary = await sessions.summary(farmer.id, scan.uuid)

    assert summary.total == 0
    assert summary.ratio_pct == 0.0
    assert summary.avg_conf == 0.0


async def test_summary_reports_latency_range(sessions, inspections, farmer, db):
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)
    for latency in (10.0, 50.0, 90.0):
        await inspections.record(scan.id, result("Healthy", 0.9, latency))
    await db.commit()

    summary = await sessions.summary(farmer.id, scan.uuid)

    assert summary.min_lat_ms == 10.0
    assert summary.max_lat_ms == 90.0
    assert summary.avg_lat_ms == 50.0


async def test_summary_is_owner_scoped(sessions, farmer, other_farmer):
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)

    assert await sessions.summary(other_farmer.id, scan.uuid) is None


async def test_close_writes_totals_from_the_rows(sessions, inspections, farmer, db):
    """Totals are recomputed, not carried from an in-memory counter.

    ``SessionManager._stats`` tracked these in the desktop process, so a crash mid-session left
    the stored totals disagreeing with the inspection rows.
    """
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)
    for decision in ("Healthy", "Aflatoxin", "Healthy"):
        await inspections.record(scan.id, result(decision))
    await db.commit()

    closed = await sessions.close(farmer.id, scan.uuid)

    assert closed.total_count == 3
    assert closed.defect_count == 1
    assert closed.end_time is not None
    assert not closed.is_open


async def test_close_is_owner_scoped(sessions, farmer, other_farmer):
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)

    assert await sessions.close(other_farmer.id, scan.uuid) is None


async def test_defect_count_constraint_holds(sessions, farmer, db):
    """``defect_count <= total_count`` is carried over from the desktop DDL."""
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)
    scan.total_count = 1
    scan.defect_count = 5

    with pytest.raises(IntegrityError):
        await db.commit()


async def test_history_is_newest_first(sessions, farmer):
    for index in range(3):
        await sessions.create(farmer.id, f"BATCH_{index}", 0.5)

    history = await sessions.list_for_user(farmer.id)

    assert [s.batch_id for s in history] == ["BATCH_2", "BATCH_1", "BATCH_0"]


async def test_history_excludes_other_farmers(sessions, farmer, other_farmer):
    await sessions.create(farmer.id, "MINE", 0.5)
    await sessions.create(other_farmer.id, "THEIRS", 0.5)

    history = await sessions.list_for_user(farmer.id)

    assert [s.batch_id for s in history] == ["MINE"]


async def test_history_pagination_walks_backwards(sessions, farmer):
    for index in range(5):
        await sessions.create(farmer.id, f"BATCH_{index}", 0.5)

    first_page = await sessions.list_for_user(farmer.id, limit=2)
    second_page = await sessions.list_for_user(
        farmer.id, limit=2, before_id=first_page[-1].id
    )

    assert [s.batch_id for s in first_page] == ["BATCH_4", "BATCH_3"]
    assert [s.batch_id for s in second_page] == ["BATCH_2", "BATCH_1"]


async def test_open_session_lookup_finds_the_live_one(sessions, farmer):
    closed = await sessions.create(farmer.id, "BATCH_OLD", 0.5)
    await sessions.close(farmer.id, closed.uuid)
    live = await sessions.create(farmer.id, "BATCH_NEW", 0.5)

    found = await sessions.open_session_for_user(farmer.id)

    assert found.uuid == live.uuid


async def test_delete_cascades_to_inspections(sessions, inspections, farmer, db):
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)
    await inspections.record(scan.id, result())
    await db.commit()

    assert await sessions.delete(farmer.id, scan.uuid) is True
    await db.commit()

    assert await inspections.count_for_session(scan.id) == 0


async def test_delete_is_owner_scoped(sessions, farmer, other_farmer, db):
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)
    await db.commit()

    assert await sessions.delete(other_farmer.id, scan.uuid) is False

    remaining = await db.execute(select(ScanSession).where(ScanSession.uuid == scan.uuid))
    assert remaining.scalar_one_or_none() is not None


async def test_conf_threshold_is_snapshotted(sessions, farmer):
    """A later slider change must not reinterpret already-written records."""
    scan = await sessions.create(farmer.id, "BATCH_A", conf_threshold=0.73)

    found = await sessions.get(farmer.id, scan.uuid)

    assert found.conf_threshold == pytest.approx(0.73)


async def test_completed_session_metadata_can_be_updated(sessions, farmer, db):
    scan = await sessions.create(farmer.id, "BATCH_OLD", 0.5, "Old camera")
    await sessions.close(farmer.id, scan.uuid)

    updated = await sessions.update_metadata(
        farmer.id,
        scan.uuid,
        batch_id="BATCH_NEW",
        device_label="New camera",
        total_count=10,
        defect_count=3,
    )
    await db.commit()

    assert updated.batch_id == "BATCH_NEW"
    assert updated.device_label == "New camera"
    assert updated.total_count == 0
    assert updated.defect_count == 0
    assert updated.effective_total_count == 10
    assert updated.effective_defect_count == 3
    assert updated.is_manually_corrected


async def test_range_totals_use_session_aggregates(
    sessions, inspections, farmer, db
):
    from datetime import UTC, datetime, timedelta

    scan = await sessions.create(farmer.id, "BATCH_RANGE", 0.5)
    await inspections.record(scan.id, result("Healthy", confidence=0.8))
    await inspections.record(scan.id, result("Aflatoxin", confidence=0.6))
    await sessions.update_metadata(
        farmer.id,
        scan.uuid,
        batch_id=scan.batch_id,
        device_label=None,
        total_count=5,
        defect_count=2,
    )
    await db.commit()

    now = datetime.now(UTC)
    totals = await sessions.totals_between(
        farmer.id,
        now - timedelta(days=1),
        now + timedelta(days=1),
    )

    assert totals.total_figs == 5
    assert totals.healthy_count == 3
    assert totals.aflatoxin_count == 2
    assert totals.mean_confidence == pytest.approx(0.7)
