"""Scan session persistence.

Ported from ``data/session_dao.py``. Two things change beyond the move to async SQLAlchemy:

* **Every lookup is scoped by ``user_id``.** The desktop DAO took a bare ``session_id``
  because there was only one operator. Here, taking an id without an owner is how one farmer
  reads another's harvest data, so ``user_id`` is a required argument on every read.
* **``SUM(decision = 'Aflatoxin')`` is gone.** That expression (session_dao.py:51) relies on
  SQLite evaluating a boolean as 0/1; PostgreSQL rejects it outright. It is replaced by
  ``COUNT(*) FILTER (WHERE ...)``, which is standard SQL and runs on both.
"""

from __future__ import annotations

import uuid as uuid_module
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import DECISION_AFLATOXIN, DECISION_HEALTHY
from app.domain.report import InspectionMetric
from app.infra.db.models import Inspection, ScanSession

# batch_id is second-resolution, so a fast restart needs at most a couple of suffixes.
MAX_BATCH_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """Aggregate statistics. Mirrors ``SessionDAO.get_summary`` (session_dao.py:42)."""

    session_id: int
    uuid: uuid_module.UUID
    batch_id: str
    start_time: datetime
    end_time: datetime | None
    total: int
    aflatoxin: int
    healthy: int
    ratio_pct: float
    avg_conf: float
    avg_lat_ms: float
    min_lat_ms: float
    max_lat_ms: float


@dataclass(frozen=True, slots=True)
class RangeTotals:
    """Cross-session totals for a date window."""

    sessions: int
    total_figs: int
    healthy_count: int
    aflatoxin_count: int
    defect_rate_pct: float
    mean_confidence: float


@dataclass(frozen=True, slots=True)
class ExportRow:
    """One CSV line. Column order matches the desktop export (session_dao.py:100)."""

    inspection_id: int
    fig_seq: int
    batch_id: str
    timestamp: datetime
    decision: str
    confidence: float
    latency_ms: float
    image_key: str | None


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Writes ────────────────────────────────────────────────────────────

    async def create(
        self,
        user_id: int,
        batch_id: str,
        conf_threshold: float,
        device_label: str | None = None,
    ) -> ScanSession:
        """Open a session.

        Retries once with a numeric suffix on collision: ``batch_id`` is second-resolution, so
        one user double-clicking "start" inside the same second would otherwise hit
        ``uq_sessions_user_batch``.
        """
        for attempt in range(MAX_BATCH_ATTEMPTS):
            candidate = batch_id if attempt == 0 else f"{batch_id}_{attempt + 1}"
            scan_session = ScanSession(
                uuid=uuid_module.uuid4(),
                user_id=user_id,
                batch_id=candidate,
                device_label=device_label,
                conf_threshold=conf_threshold,
                start_time=datetime.now(UTC),
            )
            try:
                # A SAVEPOINT, not a plain flush. On collision only this INSERT is undone;
                # a bare session.rollback() here would discard the caller's whole
                # transaction, including sessions and inspections written earlier in it.
                async with self._session.begin_nested():
                    self._session.add(scan_session)
                    await self._session.flush()
            except IntegrityError:
                continue
            return scan_session

        raise RuntimeError(f"Could not allocate a batch_id for user {user_id}")

    async def close(self, user_id: int, session_uuid: uuid_module.UUID) -> ScanSession | None:
        """Stamp ``end_time`` and write the final counts.

        Totals are recomputed from the ``inspections`` rows rather than trusted from an
        in-memory counter as the desktop app did (``SessionManager._stats``), so a dropped
        WebSocket cannot leave the totals disagreeing with the records.
        """
        scan_session = await self.get(user_id, session_uuid)
        if scan_session is None:
            return None

        counts = await self._session.execute(
            select(
                func.count().label("total"),
                func.count().filter(Inspection.decision == DECISION_AFLATOXIN).label("defect"),
            ).where(Inspection.session_id == scan_session.id)
        )
        total, defect = counts.one()

        scan_session.total_count = total or 0
        scan_session.defect_count = defect or 0
        scan_session.end_time = datetime.now(UTC)

        await self._session.flush()
        return scan_session

    async def delete(self, user_id: int, session_uuid: uuid_module.UUID) -> bool:
        """Delete a session and, by cascade, its inspections."""
        result = await self._session.execute(
            delete(ScanSession).where(
                ScanSession.uuid == session_uuid, ScanSession.user_id == user_id
            )
        )
        return bool(result.rowcount)

    # ── Reads ─────────────────────────────────────────────────────────────

    async def get(self, user_id: int, session_uuid: uuid_module.UUID) -> ScanSession | None:
        result = await self._session.execute(
            select(ScanSession).where(
                ScanSession.uuid == session_uuid, ScanSession.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def owns_session(self, user_id: int, session_id: int) -> bool:
        """Whether this user owns the session with the given primary key."""
        result = await self._session.execute(
            select(ScanSession.id).where(
                ScanSession.id == session_id, ScanSession.user_id == user_id
            )
        )
        return result.scalar_one_or_none() is not None

    async def list_for_user(
        self, user_id: int, limit: int = 25, before_id: int | None = None
    ) -> list[ScanSession]:
        """Newest first, cursor-paginated on the primary key."""
        query = select(ScanSession).where(ScanSession.user_id == user_id)

        if before_id is not None:
            query = query.where(ScanSession.id < before_id)

        result = await self._session.execute(
            query.order_by(ScanSession.id.desc()).limit(limit)
        )
        return list(result.scalars())

    async def open_session_for_user(self, user_id: int) -> ScanSession | None:
        """The user's currently open session, if any."""
        result = await self._session.execute(
            select(ScanSession)
            .where(ScanSession.user_id == user_id, ScanSession.end_time.is_(None))
            .order_by(ScanSession.id.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def summary(
        self, user_id: int, session_uuid: uuid_module.UUID
    ) -> SessionSummary | None:
        scan_session = await self.get(user_id, session_uuid)
        if scan_session is None:
            return None

        result = await self._session.execute(
            select(
                func.count().label("total"),
                func.count().filter(Inspection.decision == DECISION_AFLATOXIN).label("aflatoxin"),
                func.count().filter(Inspection.decision == DECISION_HEALTHY).label("healthy"),
                func.avg(Inspection.confidence).label("avg_conf"),
                func.avg(Inspection.latency_ms).label("avg_lat"),
                func.min(Inspection.latency_ms).label("min_lat"),
                func.max(Inspection.latency_ms).label("max_lat"),
            ).where(Inspection.session_id == scan_session.id)
        )
        row = result.one()
        total = row.total or 0
        aflatoxin = row.aflatoxin or 0

        return SessionSummary(
            session_id=scan_session.id,
            uuid=scan_session.uuid,
            batch_id=scan_session.batch_id,
            start_time=scan_session.start_time,
            end_time=scan_session.end_time,
            total=total,
            aflatoxin=aflatoxin,
            healthy=row.healthy or 0,
            ratio_pct=round(aflatoxin / total * 100, 2) if total else 0.0,
            avg_conf=round(row.avg_conf or 0.0, 4),
            avg_lat_ms=round(row.avg_lat or 0.0, 1),
            min_lat_ms=round(row.min_lat or 0.0, 1),
            max_lat_ms=round(row.max_lat or 0.0, 1),
        )

    async def fetch_metrics(
        self, user_id: int, session_uuid: uuid_module.UUID
    ) -> list[InspectionMetric] | None:
        """The three columns a report needs, for every fig in the session.

        Returns ``None`` when the session does not exist or is not this user's, so the caller
        can tell "no such session" from "session with no figs".

        Loads the rows rather than aggregating in SQL: percentiles and histograms have no
        portable SQL form (SQLite has no percentile function), and keeping every query
        dialect-agnostic has already caught one portability bug here. A batch is bounded by
        what fits on a conveyor in one run, so this stays in the tens of thousands of rows.
        """
        scan_session = await self.get(user_id, session_uuid)
        if scan_session is None:
            return None

        result = await self._session.execute(
            select(
                Inspection.decision,
                Inspection.confidence,
                Inspection.latency_ms,
            )
            .where(Inspection.session_id == scan_session.id)
            .order_by(Inspection.fig_seq)
        )

        return [
            InspectionMetric(
                decision=row.decision,
                confidence=row.confidence,
                latency_ms=row.latency_ms,
            )
            for row in result
        ]

    async def totals_between(
        self, user_id: int, start: datetime, end: datetime
    ) -> RangeTotals:
        """Aggregate every session the user started within the window.

        Aggregated in SQL because it is plain counting, which is portable — unlike the
        percentile work in :meth:`fetch_metrics`.
        """
        result = await self._session.execute(
            select(
                func.count(func.distinct(ScanSession.id)).label("sessions"),
                func.count(Inspection.id).label("figs"),
                func.count(Inspection.id)
                .filter(Inspection.decision == DECISION_AFLATOXIN)
                .label("aflatoxin"),
                func.count(Inspection.id)
                .filter(Inspection.decision == DECISION_HEALTHY)
                .label("healthy"),
                func.avg(Inspection.confidence).label("avg_conf"),
            )
            .select_from(ScanSession)
            .outerjoin(Inspection, Inspection.session_id == ScanSession.id)
            .where(
                ScanSession.user_id == user_id,
                ScanSession.start_time >= start,
                ScanSession.start_time <= end,
            )
        )
        row = result.one()
        figs = row.figs or 0
        aflatoxin = row.aflatoxin or 0

        return RangeTotals(
            sessions=row.sessions or 0,
            total_figs=figs,
            healthy_count=row.healthy or 0,
            aflatoxin_count=aflatoxin,
            defect_rate_pct=round(aflatoxin / figs * 100, 2) if figs else 0.0,
            mean_confidence=round(row.avg_conf or 0.0, 4),
        )

    async def iter_export_rows(
        self, user_id: int, session_uuid: uuid_module.UUID, chunk_size: int = 500
    ) -> AsyncIterator[ExportRow]:
        """Stream export rows in fig order.

        The desktop export built the whole result set in memory and wrote a file to disk
        (session_dao.py:80). Streaming keeps a large harvest from being buffered per request.
        """
        scan_session = await self.get(user_id, session_uuid)
        if scan_session is None:
            return

        query = (
            select(
                Inspection.id,
                Inspection.fig_seq,
                Inspection.timestamp,
                Inspection.decision,
                Inspection.confidence,
                Inspection.latency_ms,
                Inspection.image_key,
            )
            .where(Inspection.session_id == scan_session.id)
            .order_by(Inspection.fig_seq)
            .execution_options(yield_per=chunk_size)
        )

        result = await self._session.stream(query)
        async for row in result:
            yield ExportRow(
                inspection_id=row.id,
                fig_seq=row.fig_seq,
                batch_id=scan_session.batch_id,
                timestamp=row.timestamp,
                decision=row.decision,
                confidence=row.confidence,
                latency_ms=row.latency_ms,
                image_key=row.image_key,
            )
