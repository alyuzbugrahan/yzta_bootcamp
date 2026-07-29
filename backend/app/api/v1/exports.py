"""CSV export.

Replaces the desktop "Export CSV" button, which wrote a file under ``data/exports/`` and popped
a dialog showing its path (ui/main_window.py:596). A server-side path is meaningless to a
browser, so rows stream straight into the response body as a download.
"""

from __future__ import annotations

import uuid as uuid_module
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.errors import NotFound
from app.deps import CurrentUser, SessionFactory
from app.infra.repositories.session_repository import SessionRepository
from app.services.csv_export import export_filename, stream_csv

router = APIRouter(tags=["exports"])


@router.get("/sessions/{session_uuid}/export.csv", response_class=StreamingResponse)
async def export_session_csv(
    session_uuid: uuid_module.UUID,
    user: CurrentUser,
    factory: SessionFactory,
    request: Request,
) -> StreamingResponse:
    """Stream the session's inspections as CSV.

    Opens its own database session rather than using the request-scoped ``get_db``. A
    ``yield`` dependency is torn down before a streaming body finishes being produced, so the
    shared session would already be closed by the time the generator ran. Existence is checked
    up front so a missing session still returns a JSON 404 rather than an empty download.
    """
    async with factory() as db:
        scan = await SessionRepository(db).get(user.id, session_uuid)
        if scan is None:
            raise NotFound("Session not found")
        batch_id = scan.batch_id

    base_url = str(request.base_url).rstrip("/") + "/api/v1"

    async def body() -> AsyncIterator[str]:
        async with factory() as db:
            rows = SessionRepository(db).iter_export_rows(user.id, session_uuid)
            async for chunk in stream_csv(rows, image_url_base=base_url):
                yield chunk

    return StreamingResponse(
        body(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{export_filename(batch_id)}"',
            # The row count is not known before streaming starts, so the browser cannot show
            # download progress. Streaming a season of records beats buffering it to find out.
            "Cache-Control": "no-store",
        },
    )
