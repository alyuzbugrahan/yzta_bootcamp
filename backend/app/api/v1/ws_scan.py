"""The realtime scanning endpoint.

Protocol (see docs/WEB_MIGRATION_PLAN.md §4.2):

* client → server: a **binary** message is one JPEG frame; a **text** message is JSON control
  (``set_conf``, ``pause``, ``resume``).
* server → client: ``frame``, ``inspection``, ``stats``, ``error`` — all JSON.

Boxes are returned as normalised coordinates for the browser to draw. The desktop pipeline
burned them into the image with ``cv2.rectangle`` (video_processor_worker.py:237); shipping an
annotated JPEG back would roughly double bandwidth to redraw something the client can already
draw from 40 bytes of numbers.
"""

from __future__ import annotations

import uuid as uuid_module

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.core.logging import get_logger
from app.core.security import TokenError, decode_token, is_revoked
from app.domain.gating import Timings
from app.domain.models import FrameOutcome, InspectionResult
from app.domain.pipeline import ScanPipeline
from app.infra.detector_provider import pipeline_detector
from app.infra.repositories.inspection_repository import InspectionRepository
from app.infra.repositories.session_repository import SessionRepository
from app.infra.repositories.user_repository import UserRepository
from app.infra.storage.base import image_key
from app.services.frame_codec import FrameLimits
from app.services.scan_service import ScanConnection

log = get_logger(__name__)

router = APIRouter(tags=["scan"])

# RFC 6455 application close codes.
WS_UNAUTHORIZED = 4401
WS_FORBIDDEN = 4403
WS_NOT_FOUND = 4404
WS_CONFLICT = 4409
WS_SESSION_CLOSED = 4410
WS_TOO_MANY = 4429


@router.websocket("/ws/scan/{session_uuid}")
async def scan(
    websocket: WebSocket,
    session_uuid: uuid_module.UUID,
    ticket: str = Query(description="Short-lived ticket from POST /sessions/{uuid}/ticket"),
) -> None:
    app = websocket.app
    settings = app.state.settings

    # ── Authenticate before accepting ─────────────────────────────────────
    try:
        claims = decode_token(ticket, "ws", settings.auth)
    except TokenError:
        await websocket.close(code=WS_UNAUTHORIZED, reason="Invalid or expired ticket")
        return

    # A ticket names exactly one session. Without this check any valid ticket would open a
    # socket onto any session id the holder cared to type.
    if claims.session_uuid != str(session_uuid):
        await websocket.close(code=WS_FORBIDDEN, reason="Ticket is for a different session")
        return

    factory = app.state.session_factory

    async with factory() as db:
        # "Log out everywhere" must also drop sockets, not just HTTP calls. A ticket lives 60
        # seconds, so without this an already-minted one would outlive its own revocation.
        user = await UserRepository(db).get_by_id(claims.user_id)

        if user is None or not user.is_active or is_revoked(claims, user):
            await websocket.close(code=WS_UNAUTHORIZED, reason="Ticket is no longer valid")
            return

        scan_session = await SessionRepository(db).get(claims.user_id, session_uuid)

        if scan_session is None:
            await websocket.close(code=WS_NOT_FOUND, reason="Session not found")
            return

        if not scan_session.is_open:
            await websocket.close(code=WS_SESSION_CLOSED, reason="Session is closed")
            return

        session_id = scan_session.id
        batch_id = scan_session.batch_id
        conf_threshold = scan_session.conf_threshold

    connection = ScanConnection(
        session_uuid=session_uuid,
        session_id=session_id,
        user_id=claims.user_id,
        pipeline=ScanPipeline(
            detector=pipeline_detector(app.state.detector),
            timings=Timings.from_settings(settings.timing),
        ),
        pool=app.state.inference_pool,
        limits=FrameLimits.from_settings(settings.ingest),
        batch_id=batch_id,
        conf_threshold=conf_threshold,
        iou_threshold=settings.model.iou_threshold,
        max_fps=settings.ingest.max_fps,
    )

    registry = app.state.connection_registry
    connection_limiter = getattr(app.state, "connection_limiter", None)
    user_key = str(claims.user_id)

    # Per-user cap first: one farmer opening sockets in a loop must not be able to exhaust the
    # inference pool for everyone else, even across different sessions of their own.
    if connection_limiter is not None and not connection_limiter.acquire(user_key):
        await websocket.close(code=WS_TOO_MANY, reason="Too many concurrent connections")
        return

    if not await registry.acquire(connection):
        if connection_limiter is not None:
            connection_limiter.release(user_key)
        await websocket.close(code=WS_CONFLICT, reason="Session already has a live connection")
        return

    await websocket.accept()

    async def send(payload: dict) -> None:
        if websocket.client_state is WebSocketState.CONNECTED:
            await websocket.send_json(payload)

    async def on_outcome(outcome: FrameOutcome) -> None:
        await send(
            {
                "type": "frame",
                "latency_ms": outcome.stats.latency_ms,
                "detections": [
                    {
                        "class_name": d.class_name,
                        "confidence": round(d.confidence, 4),
                        "bbox": list(d.bbox),
                    }
                    for d in outcome.detections
                ],
                "stats": {
                    "active_slots": outcome.stats.active_slots,
                    "locked_slots": outcome.stats.locked_slots,
                    "effective_fps": connection.effective_fps,
                    **connection.counters.as_dict(),
                },
            }
        )

    async def on_inspection(inspection: InspectionResult, frame_bytes: bytes) -> None:
        """Persist the fig, archive its frame, then tell the client what was recorded.

        A separate database session per inspection: the connection is long-lived, and holding
        one transaction open for an entire scanning session would pin a pooled connection for
        hours and leave every write invisible until the farmer stopped.
        """
        archiver = app.state.archiver

        async with factory() as db:
            repository = InspectionRepository(db)
            saved = await repository.record(session_id, inspection)

            if archiver is not None:
                # The key embeds fig_seq, which the insert allocates — so the row must exist
                # before there is a key to store.
                key = image_key(claims.user_id, batch_id, saved.fig_seq, saved.decision)
                if archiver.enqueue(key, frame_bytes):
                    await repository.set_image_key(saved.id, key)
                    saved.image_key = key

            await db.commit()

        await send(
            {
                "type": "inspection",
                "fig_seq": saved.fig_seq,
                "decision": saved.decision,
                "confidence": saved.confidence,
                "latency_ms": saved.latency_ms,
                "timestamp": saved.timestamp.isoformat(),
                "image_url": (
                    f"/api/v1/inspections/{saved.id}/image" if saved.image_key else None
                ),
            }
        )

        await send({"type": "stats", **connection.counters.as_dict()})

    async def on_error(code: str, message: str) -> None:
        await send({"type": "error", "code": code, "message": message})

    async def on_dropped(reason: str) -> None:
        """Acknowledge a frame the server chose not to process.

        Without this a self-clocking client waits forever for a reply that is never coming.
        The message is deliberately tiny — drops are the common case under load, and the
        whole point is to spend nothing on them.
        """
        await send({"type": "dropped", "reason": reason})

    connection.on_dropped = on_dropped
    connection.on_outcome = on_outcome
    connection.on_inspection = on_inspection
    connection.on_error = on_error
    connection.start()

    log.info("scan_connected", user_id=claims.user_id, session=str(session_uuid))

    try:
        await _pump(websocket, connection, on_error)
    except WebSocketDisconnect:
        pass
    finally:
        await connection.stop()
        await registry.release(session_uuid)
        if connection_limiter is not None:
            connection_limiter.release(user_key)
        log.info(
            "scan_disconnected",
            user_id=claims.user_id,
            session=str(session_uuid),
            **connection.counters.as_dict(),
        )


async def _pump(websocket: WebSocket, connection: ScanConnection, on_error) -> None:
    """Read client messages until the socket closes.

    Deliberately does no work beyond handing frames to the connection: anything slow here would
    stop the socket being drained, and the client's frames would back up in the kernel buffer —
    reintroducing the lag the single-slot mailbox exists to prevent.

    The session is *not* closed on disconnect. A dropped mobile connection is normal, and
    reconnecting resumes the same session with its ``fig_seq`` series intact. Finalising totals
    is an explicit act: ``POST /sessions/{uuid}/stop``.
    """
    while True:
        message = await websocket.receive()

        if message["type"] == "websocket.disconnect":
            return

        if (data := message.get("bytes")) is not None:
            reason = connection.submit(data)
            if reason is not None and connection.on_dropped is not None:
                await connection.on_dropped(reason)
            continue

        text = message.get("text")
        if text is not None:
            await _control(text, connection, on_error)


async def _control(text: str, connection: ScanConnection, on_error) -> None:
    import json

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        await on_error("BAD_CONTROL", "Control message is not valid JSON")
        return

    match payload.get("type"):
        case "set_conf":
            value = payload.get("value")
            if isinstance(value, int | float) and 0.0 <= value <= 1.0:
                connection.set_conf_threshold(float(value))
            else:
                await on_error("BAD_CONTROL", "set_conf requires a value between 0 and 1")
        case "pause":
            connection.paused = True
        case "resume":
            connection.paused = False
        case _:
            await on_error("BAD_CONTROL", f"Unknown control type {payload.get('type')!r}")
