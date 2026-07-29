"""Inspection persistence, focused on database-side ``fig_seq`` allocation."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.models import Detection, InspectionResult


def result(decision: str = "Healthy", confidence: float = 0.9, latency: float = 50.0):
    return InspectionResult(
        decision=decision,
        confidence=confidence,
        detection=Detection(decision, confidence, (0.2, 0.2, 0.6, 0.6)),
        latency_ms=latency,
    )


async def test_first_fig_is_sequence_one(sessions, inspections, farmer):
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)

    recorded = await inspections.record(scan.id, result())

    assert recorded.fig_seq == 1


async def test_sequence_increments(sessions, inspections, farmer):
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)

    sequences = [(await inspections.record(scan.id, result())).fig_seq for _ in range(5)]

    assert sequences == [1, 2, 3, 4, 5]


async def test_sequences_are_independent_per_session(sessions, inspections, farmer):
    a = await sessions.create(farmer.id, "BATCH_A", 0.5)
    b = await sessions.create(farmer.id, "BATCH_B", 0.5)

    await inspections.record(a.id, result())
    await inspections.record(a.id, result())
    first_in_b = await inspections.record(b.id, result())

    assert first_in_b.fig_seq == 1


async def test_sequence_resumes_after_reconnection(sessions, inspections, farmer, db):
    """The bug an in-memory counter would reintroduce.

    ``SessionManager._fig_counter`` lived in the desktop process. A farmer whose WebSocket
    drops and reconnects gets a fresh pipeline object, so an in-memory counter would restart
    at 1 and collide with their own earlier records. Deriving it from the table means the new
    connection simply continues.
    """
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)
    for _ in range(3):
        await inspections.record(scan.id, result())
    await db.commit()

    # A new repository instance stands in for the reconnected connection.
    from app.infra.repositories.inspection_repository import InspectionRepository

    resumed = await InspectionRepository(db).record(scan.id, result())

    assert resumed.fig_seq == 4


async def test_duplicate_sequence_is_rejected(sessions, inspections, farmer, db):
    """``uq_inspections_session_seq`` is what makes the allocation safe under concurrency."""
    from app.infra.db.models import Inspection

    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)
    await inspections.record(scan.id, result())
    await db.commit()

    db.add(
        Inspection(
            session_id=scan.id,
            fig_seq=1,
            timestamp=result().timestamp,
            decision="Healthy",
            confidence=0.5,
            latency_ms=10.0,
        )
    )

    with pytest.raises(IntegrityError):
        await db.commit()


async def test_decision_check_constraint_rejects_unknown_labels(
    sessions, inspections, farmer, db
):
    """The desktop DDL constrained this column; an 'Unknown' class must not reach the table."""
    from app.infra.db.models import Inspection

    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)

    db.add(
        Inspection(
            session_id=scan.id,
            fig_seq=1,
            timestamp=result().timestamp,
            decision="Unknown",
            confidence=0.5,
            latency_ms=10.0,
        )
    )

    with pytest.raises(IntegrityError):
        await db.commit()


async def test_confidence_out_of_range_is_rejected(sessions, farmer, db):
    from app.infra.db.models import Inspection

    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)

    db.add(
        Inspection(
            session_id=scan.id,
            fig_seq=1,
            timestamp=result().timestamp,
            decision="Healthy",
            confidence=1.5,
            latency_ms=10.0,
        )
    )

    with pytest.raises(IntegrityError):
        await db.commit()


async def test_image_key_is_stored(sessions, inspections, farmer):
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)

    recorded = await inspections.record(
        scan.id, result(), image_key="u1/BATCH_A/fig_0001_Healthy.jpg"
    )

    assert recorded.image_key == "u1/BATCH_A/fig_0001_Healthy.jpg"


async def test_listing_is_newest_fig_first(sessions, inspections, farmer, db):
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)
    for _ in range(4):
        await inspections.record(scan.id, result())
    await db.commit()

    rows = await inspections.list_for_session(scan.id, limit=2)

    assert [row.fig_seq for row in rows] == [4, 3]


async def test_listing_pagination(sessions, inspections, farmer, db):
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)
    for _ in range(5):
        await inspections.record(scan.id, result())
    await db.commit()

    page = await inspections.list_for_session(scan.id, limit=2, before_seq=4)

    assert [row.fig_seq for row in page] == [3, 2]


async def test_concurrent_records_do_not_duplicate_sequences(engine, farmer):
    """Two writers racing on the same session must still produce distinct sequence numbers.

    Serialised through the constraint plus retry rather than by locking. On SQLite this
    exercises the retry path; on PostgreSQL it is the same code path under real concurrency.
    """
    from app.infra.db.session import create_session_factory
    from app.infra.repositories.inspection_repository import InspectionRepository
    from app.infra.repositories.session_repository import SessionRepository

    factory = create_session_factory(engine)

    async with factory() as setup:
        scan = await SessionRepository(setup).create(farmer.id, "BATCH_RACE", 0.5)
        await setup.commit()
        scan_id = scan.id

    async def write() -> int:
        async with factory() as db:
            recorded = await InspectionRepository(db).record(scan_id, result())
            await db.commit()
            return recorded.fig_seq

    sequences = await asyncio.gather(*(write() for _ in range(4)), return_exceptions=True)
    successful = [s for s in sequences if isinstance(s, int)]

    assert len(successful) == len(set(successful)), f"duplicate fig_seq: {successful}"
