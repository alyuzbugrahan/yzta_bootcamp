"""Behaviour that differs between SQLite and PostgreSQL.

These cover the specific things that a SQLite-only run cannot prove. They are written to pass on
both dialects, so they are meaningful only when the suite is pointed at PostgreSQL::

    FIGION_TEST_DATABASE_URL=postgresql+asyncpg://figion:figion@127.0.0.1:55432/figion_test

``test_dialect_in_use`` reports which backend actually ran, so a green suite cannot be mistaken
for PostgreSQL coverage it did not have.
"""

from __future__ import annotations

import uuid as uuid_module

import pytest
from sqlalchemy import select, text

from app.domain.models import Detection, InspectionResult
from app.infra.db.models import Inspection, ScanSession


def result(decision: str = "Healthy"):
    return InspectionResult(
        decision=decision,
        confidence=0.9,
        detection=Detection(decision, 0.9, (0.2, 0.2, 0.6, 0.6)),
        latency_ms=50.0,
    )


async def test_dialect_in_use(db, capsys):
    """Not an assertion — a visible record of what the run actually covered."""
    with capsys.disabled():
        print(f"\n  [dialect] {db.bind.dialect.name}")

    assert db.bind.dialect.name in {"sqlite", "postgresql"}


async def test_uuid_round_trips(sessions, farmer, db):
    """On PostgreSQL this column is the native ``uuid`` type, not a string.

    A mismatch here would surface as every session lookup returning None.
    """
    scan = await sessions.create(farmer.id, "BATCH_UUID", 0.5)
    await db.commit()
    db.expunge_all()

    found = await sessions.get(farmer.id, scan.uuid)

    assert found is not None
    assert isinstance(found.uuid, uuid_module.UUID)
    assert found.uuid == scan.uuid


async def test_timestamps_are_timezone_aware(sessions, inspections, farmer, db):
    """``TIMESTAMPTZ`` must come back with tzinfo attached.

    SQLite has no native timestamp type and hands back whatever it stored, so a naive datetime
    slipping through is invisible there and becomes a comparison bug on PostgreSQL.
    """
    scan = await sessions.create(farmer.id, "BATCH_TZ", 0.5)
    await inspections.record(scan.id, result())
    await db.commit()
    db.expunge_all()

    reloaded = await sessions.get(farmer.id, scan.uuid)
    row = (
        await db.execute(select(Inspection).where(Inspection.session_id == reloaded.id))
    ).scalar_one()

    if db.bind.dialect.name == "postgresql":
        assert reloaded.start_time.tzinfo is not None
        assert row.timestamp.tzinfo is not None
    else:
        pytest.skip("SQLite has no native timestamptz; assertion is meaningless here")


async def test_cascade_delete_is_enforced_by_the_database(sessions, inspections, farmer, db):
    """Deletes the parent row in raw SQL, bypassing the ORM's own cascade.

    SQLite only enforces this with ``PRAGMA foreign_keys=ON`` set per connection, which is easy
    to lose; PostgreSQL enforces it natively. Issuing the DELETE directly proves the constraint
    is doing the work rather than SQLAlchemy's relationship bookkeeping.
    """
    scan = await sessions.create(farmer.id, "BATCH_CASCADE", 0.5)
    await inspections.record(scan.id, result())
    await inspections.record(scan.id, result())
    await db.commit()

    session_id = scan.id
    assert await inspections.count_for_session(session_id) == 2

    await db.execute(text("DELETE FROM sessions WHERE id = :id"), {"id": session_id})
    await db.commit()

    assert await inspections.count_for_session(session_id) == 0


async def test_count_filter_runs_on_this_dialect(sessions, inspections, farmer, db):
    """``COUNT(*) FILTER`` is the replacement for the SQLite-only boolean SUM.

    PostgreSQL rejects ``SUM(decision = 'Aflatoxin')`` outright, so this is the query shape the
    port depends on being portable.
    """
    scan = await sessions.create(farmer.id, "BATCH_FILTER", 0.5)
    for decision in ("Aflatoxin", "Healthy", "Aflatoxin"):
        await inspections.record(scan.id, result(decision))
    await db.commit()

    summary = await sessions.summary(farmer.id, scan.uuid)

    assert (summary.aflatoxin, summary.healthy) == (2, 1)


async def test_savepoint_isolates_a_failed_insert(sessions, inspections, farmer, db):
    """The savepoint fix, exercised on whichever dialect is configured.

    PostgreSQL aborts the whole transaction on a constraint violation unless the statement is
    wrapped in a SAVEPOINT, so this is a stronger test there than on SQLite.
    """
    first = await sessions.create(farmer.id, "BATCH_SAVEPOINT", 0.5)
    await inspections.record(first.id, result())

    second = await sessions.create(farmer.id, "BATCH_SAVEPOINT", 0.5)
    await db.commit()

    assert second.batch_id == "BATCH_SAVEPOINT_2"
    assert await inspections.count_for_session(first.id) == 1

    surviving = await db.execute(
        select(ScanSession).where(ScanSession.user_id == farmer.id)
    )
    assert len(list(surviving.scalars())) == 2
