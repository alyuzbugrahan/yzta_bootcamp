"""Serving archived inspection images.

The desktop viewer opened the file directly from ``inspections.image_path`` (ui/db_viewer.py).
A browser cannot, so the bytes are served here — behind the same ownership check as everything
else, because an inspection image is a photograph of a farmer's crop.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from app.core.errors import NotFound
from app.deps import CurrentUser, DbSession
from app.infra.repositories.inspection_repository import InspectionRepository
from app.infra.repositories.session_repository import SessionRepository
from app.infra.storage.base import ObjectNotFound

router = APIRouter(tags=["images"])


@router.get("/inspections/{inspection_id}/image")
async def get_inspection_image(
    inspection_id: int,
    user: CurrentUser,
    db: DbSession,
    request: Request,
) -> Response:
    """Return the source frame for one inspection.

    Ownership is resolved by walking the inspection back to its session and checking that
    session belongs to the caller — the inspection id alone proves nothing. As elsewhere,
    someone else's image is a 404 rather than a 403, so ids cannot be probed.
    """
    storage = request.app.state.storage

    if storage is None:
        raise NotFound("Image archiving is disabled")

    inspection = await InspectionRepository(db).get(inspection_id)

    if inspection is None or inspection.image_key is None:
        raise NotFound("Image not found")

    owns = await SessionRepository(db).owns_session(user.id, inspection.session_id)
    if not owns:
        raise NotFound("Image not found")

    # S3 can hand the browser a short-lived direct URL, which keeps thumbnail traffic off the
    # API. Local storage has no such URL, so the bytes are streamed instead.
    url = storage.presigned_url(inspection.image_key)
    if url is not None:
        return RedirectResponse(url, status_code=307)

    try:
        data = await storage.get(inspection.image_key)
    except ObjectNotFound as exc:
        # The row says an image exists but the object is gone — dropped by a full archive
        # queue, or removed by a retention policy.
        raise NotFound("Image is no longer stored") from exc

    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": f'inline; filename="fig_{inspection.fig_seq:04d}.jpg"',
        },
    )
