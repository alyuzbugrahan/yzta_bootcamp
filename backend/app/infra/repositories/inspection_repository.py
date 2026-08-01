"""Inspection persistence.

Ported from ``data/inspection_repository.py``, with ``fig_seq`` allocation moved into the
database.

The desktop version incremented ``SessionManager._fig_counter`` in memory
(data/session_manager.py:34). That works when one process owns the session for its whole
lifetime, but a web session outlives any single connection: a farmer whose WebSocket drops and
reconnects would restart at 1 and collide with their own earlier records. The counter is
therefore derived from the table, and ``uq_inspections_session_seq`` is what makes it safe
under concurrency.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import DECISION_AFLATOXIN, InspectionResult
from app.infra.db.models import Inspection, ScanSession

MAX_ALLOCATION_ATTEMPTS = 3


class InspectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        session_id: int,
        result: InspectionResult,
        image_key: str | None = None,
    ) -> InspectionResult:
        """Insert one inspection, allocating ``fig_seq`` atomically.

        ``fig_seq`` comes from a scalar subquery evaluated inside the INSERT, so no read is
        exposed between "what is the max" and "claim the next one". If two writers still race,
        the unique constraint rejects the loser and the retry recomputes.
        """
        timestamp = result.timestamp or datetime.now(UTC)

        for attempt in range(MAX_ALLOCATION_ATTEMPTS):
            next_seq = (
                select(func.coalesce(func.max(Inspection.fig_seq), 0) + 1)
                .where(Inspection.session_id == session_id)
                .scalar_subquery()
            )

            statement = (
                insert(Inspection)
                .values(
                    session_id=session_id,
                    fig_seq=next_seq,
                    timestamp=timestamp,
                    decision=result.decision,
                    confidence=result.confidence,
                    latency_ms=result.latency_ms,
                    image_key=image_key,
                )
                .returning(Inspection.id, Inspection.fig_seq)
            )

            try:
                # A SAVEPOINT, so that losing the race costs this INSERT and nothing else.
                # session.rollback() would discard every inspection recorded earlier in the
                # caller's transaction.
                async with self._session.begin_nested():
                    row = await self._session.execute(statement)
                    inspection_id, fig_seq = row.one()
            except IntegrityError:
                if attempt == MAX_ALLOCATION_ATTEMPTS - 1:
                    raise
                continue

            # Keep lightweight session aggregates current in the same transaction. Dashboard
            # totals can then read only the sessions table instead of joining every inspection.
            is_defect = 1 if result.decision == DECISION_AFLATOXIN else 0
            await self._session.execute(
                update(ScanSession)
                .where(ScanSession.id == session_id)
                .values(
                    avg_confidence=(
                        (ScanSession.avg_confidence * ScanSession.total_count)
                        + result.confidence
                    )
                    / (ScanSession.total_count + 1),
                    total_count=ScanSession.total_count + 1,
                    defect_count=ScanSession.defect_count + is_defect,
                )
            )

            result.id = inspection_id
            result.fig_seq = fig_seq
            result.image_key = image_key
            return result

        raise RuntimeError("unreachable")  # pragma: no cover

    async def set_image_key(self, inspection_id: int, image_key: str) -> None:
        """Attach the archive key after the row exists.

        Two steps because the key contains ``fig_seq``, which the database allocates during the
        insert — there is no key to write until the row is there.
        """
        await self._session.execute(
            update(Inspection).where(Inspection.id == inspection_id).values(image_key=image_key)
        )

    async def list_for_session(
        self, session_id: int, limit: int = 50, before_seq: int | None = None
    ) -> list[Inspection]:
        """Most recent fig first, cursor-paginated on ``fig_seq``.

        Callers must have already established that the session belongs to the requesting user
        — see ``SessionRepository.get``.
        """
        query = select(Inspection).where(Inspection.session_id == session_id)

        if before_seq is not None:
            query = query.where(Inspection.fig_seq < before_seq)

        result = await self._session.execute(
            query.order_by(Inspection.fig_seq.desc()).limit(limit)
        )
        return list(result.scalars())

    async def get(self, inspection_id: int) -> Inspection | None:
        return await self._session.get(Inspection, inspection_id)

    async def count_for_session(self, session_id: int) -> int:
        result = await self._session.execute(
            select(func.count()).where(Inspection.session_id == session_id)
        )
        return result.scalar_one()
