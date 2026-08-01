"""Session orchestration.

Ported from ``data/session_manager.py``. The desktop manager held the live session id, batch
id, fig counter and running statistics as instance state, because one process owned exactly one
session for its whole lifetime.

None of that state lives here. A web session is identified by its row, outlives any single
WebSocket, and may be read concurrently by the same farmer on another device — so every method
takes the identity it needs and the database is the only source of truth. That is also what
makes reconnection work: a dropped connection resumes against the same session rather than
starting a second one.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import UTC, datetime

from app.domain.models import InspectionResult
from app.infra.db.models import Inspection, ScanSession
from app.infra.repositories.inspection_repository import InspectionRepository
from app.infra.repositories.session_repository import (
    SessionRepository,
    SessionSummary,
)


def build_batch_id(moment: datetime | None = None) -> str:
    """``BATCH_YYYYmmdd_HHMMSS``, matching the desktop format (session_manager.py:24)."""
    moment = moment or datetime.now(UTC)
    return f"BATCH_{moment.strftime('%Y%m%d_%H%M%S')}"


class SessionService:
    def __init__(
        self,
        sessions: SessionRepository,
        inspections: InspectionRepository,
    ) -> None:
        self._sessions = sessions
        self._inspections = inspections

    async def start(
        self,
        user_id: int,
        conf_threshold: float,
        device_label: str | None = None,
        fig_weight_g: float | None = None,
    ) -> ScanSession:
        """Open a new session for this user."""
        return await self._sessions.create(
            user_id=user_id,
            batch_id=build_batch_id(),
            conf_threshold=conf_threshold,
            device_label=device_label,
            fig_weight_g=fig_weight_g,
        )

    async def stop(
        self, user_id: int, session_uuid: uuid_module.UUID
    ) -> ScanSession | None:
        """Close the session and write its final counts."""
        return await self._sessions.close(user_id, session_uuid)

    async def record(
        self,
        scan_session: ScanSession,
        result: InspectionResult,
        image_key: str | None = None,
    ) -> InspectionResult:
        """Persist one locked fig, assigning its sequence number."""
        return await self._inspections.record(scan_session.id, result, image_key)

    async def get(
        self, user_id: int, session_uuid: uuid_module.UUID
    ) -> ScanSession | None:
        return await self._sessions.get(user_id, session_uuid)

    async def open_session(self, user_id: int) -> ScanSession | None:
        """The user's currently open session, if any.

        A browser tab closing mid-scan leaves the row open, so callers check this before
        starting another rather than stranding the first without totals.
        """
        return await self._sessions.open_session_for_user(user_id)

    async def summary(
        self, user_id: int, session_uuid: uuid_module.UUID
    ) -> SessionSummary | None:
        return await self._sessions.summary(user_id, session_uuid)

    async def history(
        self, user_id: int, limit: int = 25, before_id: int | None = None
    ) -> list[ScanSession]:
        return await self._sessions.list_for_user(user_id, limit, before_id)

    async def inspections(
        self, scan_session: ScanSession, limit: int = 50, before_seq: int | None = None
    ) -> list[Inspection]:
        return await self._inspections.list_for_session(scan_session.id, limit, before_seq)

    async def update_metadata(
        self,
        user_id: int,
        session_uuid: uuid_module.UUID,
        *,
        batch_id: str,
        device_label: str | None,
        total_count: int | None = None,
        defect_count: int | None = None,
        fig_weight_g: float | None = None,
    ) -> ScanSession | None:
        return await self._sessions.update_metadata(
            user_id,
            session_uuid,
            batch_id=batch_id,
            device_label=device_label,
            total_count=total_count,
            defect_count=defect_count,
            fig_weight_g=fig_weight_g,
        )

    async def first_image_key(
        self, user_id: int, session_uuid: uuid_module.UUID
    ) -> str | None:
        return await self._sessions.first_image_key(user_id, session_uuid)

    async def delete(self, user_id: int, session_uuid: uuid_module.UUID) -> bool:
        return await self._sessions.delete(user_id, session_uuid)
