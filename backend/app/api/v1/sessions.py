"""Scan session lifecycle and history.

Every handler resolves the session through ``SessionService`` with the authenticated user's id.
A path parameter alone never identifies a session, and a session belonging to someone else is
reported as 404 rather than 403 — see :class:`app.core.errors.NotFound`.
"""

from __future__ import annotations

import uuid as uuid_module

from fastapi import APIRouter, Query, Request, status

from app.api.v1.schemas import (
    CreateSessionRequest,
    InspectionPage,
    InspectionResponse,
    SessionCreatedResponse,
    SessionDetailResponse,
    SessionPage,
    SessionResponse,
    SummaryResponse,
    TicketResponse,
    UpdateSessionRequest,
)
from app.core.errors import ApiError, ErrorCode, NotFound
from app.core.logging import get_logger
from app.core.security import WS_TICKET_TTL_SECONDS, create_token, token_generation
from app.deps import AppSettings, CurrentUser, DbSession, SessionServiceDep
from app.infra.storage.base import session_prefix

log = get_logger(__name__)

router = APIRouter(tags=["sessions"])

MAX_PAGE_SIZE = 100


def _ws_url(session_uuid: uuid_module.UUID) -> str:
    return f"/api/v1/ws/scan/{session_uuid}"


@router.post(
    "/sessions",
    response_model=SessionCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    payload: CreateSessionRequest,
    user: CurrentUser,
    service: SessionServiceDep,
    settings: AppSettings,
    db: DbSession,
) -> SessionCreatedResponse:
    """Open a scanning session.

    Refuses when one is already open, returning its uuid in ``detail``. The desktop app could
    not get into this state — closing the window ended the session — but a browser tab closing
    mid-scan leaves the row open, and silently starting a second session would strand the first
    with no ``end_time`` and no totals. The client either resumes that session or stops it.
    """
    existing = await service.open_session(user.id)
    if existing is not None:
        raise ApiError(
            ErrorCode.SESSION_ALREADY_OPEN,
            "A scanning session is already open",
            status.HTTP_409_CONFLICT,
            detail={"session_uuid": str(existing.uuid), "batch_id": existing.batch_id},
        )

    conf = (
        payload.conf_threshold
        if payload.conf_threshold is not None
        else settings.model.conf_threshold
    )

    scan = await service.start(
        user.id,
        conf_threshold=conf,
        device_label=payload.device_label,
        fig_weight_g=payload.fig_weight_g,
    )

    # Committed before responding: the client opens a WebSocket against this session as soon
    # as it has the uuid, and that runs on a different database session.
    await db.commit()

    log.info("session_started", user_id=user.id, batch_id=scan.batch_id)

    return SessionCreatedResponse(
        **SessionResponse.from_session(scan).model_dump(),
        ws_url=_ws_url(scan.uuid),
    )


@router.post("/sessions/{session_uuid}/stop", response_model=SessionDetailResponse)
async def stop_session(
    session_uuid: uuid_module.UUID,
    user: CurrentUser,
    service: SessionServiceDep,
    db: DbSession,
) -> SessionDetailResponse:
    """Close the session and write its totals, recomputed from the recorded rows."""
    scan = await service.get(user.id, session_uuid)
    if scan is None:
        raise NotFound("Session not found")

    if not scan.is_open:
        raise ApiError(
            ErrorCode.SESSION_CLOSED,
            "Session is already closed",
            status.HTTP_409_CONFLICT,
        )

    closed = await service.stop(user.id, session_uuid)
    summary = await service.summary(user.id, session_uuid)
    await db.commit()

    log.info("session_stopped", user_id=user.id, total=closed.total_count)

    return SessionDetailResponse(
        session=SessionResponse.from_session(closed),
        summary=SummaryResponse.from_summary(summary),
    )


@router.post("/sessions/{session_uuid}/ticket", response_model=TicketResponse)
async def create_ticket(
    session_uuid: uuid_module.UUID,
    user: CurrentUser,
    service: SessionServiceDep,
    settings: AppSettings,
) -> TicketResponse:
    """Mint a short-lived ticket for the scanning WebSocket.

    Separate from the access token because the ticket travels in a query string. Bound to this
    one session, so it cannot be replayed against another.
    """
    scan = await service.get(user.id, session_uuid)
    if scan is None:
        raise NotFound("Session not found")

    if not scan.is_open:
        raise ApiError(
            ErrorCode.SESSION_CLOSED,
            "Session is closed",
            status.HTTP_409_CONFLICT,
        )

    return TicketResponse(
        ticket=create_token(
            user.id,
            "ws",
            settings.auth,
            session_uuid=str(session_uuid),
            generation=token_generation(user),
        ),
        ws_url=_ws_url(session_uuid),
        expires_in=WS_TICKET_TTL_SECONDS,
    )


@router.get("/sessions", response_model=SessionPage)
async def list_sessions(
    user: CurrentUser,
    service: SessionServiceDep,
    limit: int = Query(default=25, ge=1, le=MAX_PAGE_SIZE),
    cursor: int | None = Query(default=None, description="Opaque cursor from a previous page"),
) -> SessionPage:
    """Newest first. Fetches one extra row to decide whether a next page exists."""
    rows = await service.history(user.id, limit=limit + 1, before_id=cursor)

    has_more = len(rows) > limit
    page = rows[:limit]

    return SessionPage(
        items=[SessionResponse.from_session(row) for row in page],
        next_cursor=page[-1].id if has_more and page else None,
    )


@router.patch("/sessions/{session_uuid}", response_model=SessionResponse)
async def update_session(
    session_uuid: uuid_module.UUID,
    payload: UpdateSessionRequest,
    user: CurrentUser,
    service: SessionServiceDep,
    db: DbSession,
) -> SessionResponse:
    """Correct user-editable metadata for a completed scan.

    Batch metadata and displayed totals can be corrected. Detector rows and raw detector
    counters remain immutable, so exports still preserve the original model evidence.
    """
    scan = await service.get(user.id, session_uuid)
    if scan is None:
        raise NotFound("Session not found")

    if scan.is_open:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            "An open scanning session cannot be edited",
            status.HTTP_409_CONFLICT,
        )

    try:
        updated = await service.update_metadata(
            user.id,
            session_uuid,
            batch_id=payload.batch_id,
            device_label=payload.device_label,
            total_count=payload.total_count,
            defect_count=payload.defect_count,
            fig_weight_g=payload.fig_weight_g,
        )
    except ValueError as exc:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            str(exc),
            status.HTTP_409_CONFLICT,
        ) from exc

    if updated is None:
        raise NotFound("Session not found")

    await db.commit()
    log.info("session_updated", user_id=user.id, session_uuid=str(session_uuid))
    return SessionResponse.from_session(updated)


@router.get("/sessions/{session_uuid}", response_model=SessionDetailResponse)
async def get_session(
    session_uuid: uuid_module.UUID,
    user: CurrentUser,
    service: SessionServiceDep,
) -> SessionDetailResponse:
    scan = await service.get(user.id, session_uuid)
    if scan is None:
        raise NotFound("Session not found")

    summary = await service.summary(user.id, session_uuid)

    return SessionDetailResponse(
        session=SessionResponse.from_session(scan),
        summary=SummaryResponse.from_summary(summary),
    )


@router.get("/sessions/{session_uuid}/inspections", response_model=InspectionPage)
async def list_inspections(
    session_uuid: uuid_module.UUID,
    user: CurrentUser,
    service: SessionServiceDep,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    cursor: int | None = Query(default=None, description="fig_seq to page back from"),
) -> InspectionPage:
    scan = await service.get(user.id, session_uuid)
    if scan is None:
        raise NotFound("Session not found")

    rows = await service.inspections(scan, limit=limit + 1, before_seq=cursor)

    has_more = len(rows) > limit
    page = rows[:limit]

    return InspectionPage(
        items=[InspectionResponse.model_validate(row) for row in page],
        next_cursor=page[-1].fig_seq if has_more and page else None,
    )


@router.delete("/sessions/{session_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_uuid: uuid_module.UUID,
    user: CurrentUser,
    service: SessionServiceDep,
    request: Request,
    db: DbSession,
) -> None:
    """Delete a session, its inspections (by cascade), and its archived images.

    The batch id is read before the row goes, since it is half of the storage prefix. Objects
    are removed after the rows commit: an orphaned object costs storage, whereas a row pointing
    at a deleted object is a broken image in the farmer's history.
    """
    scan = await service.get(user.id, session_uuid)
    if scan is None:
        raise NotFound("Session not found")

    # If the user renamed the batch, archived image keys still contain the original batch
    # segment. Recover the real prefix from one image so deletion does not leave orphaned files.
    first_image_key = await service.first_image_key(user.id, session_uuid)
    storage_prefix = (
        f"{first_image_key.rsplit('/', 1)[0]}/"
        if first_image_key
        else session_prefix(user.id, scan.batch_id)
    )

    if not await service.delete(user.id, session_uuid):
        raise NotFound("Session not found")

    # The rows must be durable before their objects are removed; otherwise a failure between
    # the two leaves the farmer's history pointing at images that no longer exist.
    await db.commit()

    storage = request.app.state.storage
    removed = 0
    if storage is not None:
        removed = await storage.delete_prefix(storage_prefix)

    log.info(
        "session_deleted",
        user_id=user.id,
        session_uuid=str(session_uuid),
        images_removed=removed,
    )
