"""Batch reports: JSON for the UI, PDF for a buyer.

The desktop app's answer to "how did this batch do" was the stat cards on the right-hand panel
(ui/main_window.py:212) — live, in-memory, and gone when the window closed. A report is the
durable version: it can be reopened months later and handed to someone who was not standing at
the machine.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import anyio.to_thread
from fastapi import APIRouter, Query, Response

from app.api.v1.schemas import RangeReportResponse, SessionReportResponse
from app.core.errors import ApiError, ErrorCode, NotFound
from app.deps import AppSettings, CurrentUser, SessionRepositoryDep
from app.domain.report import build_session_report
from app.services.pdf_report import render_session_report

router = APIRouter(tags=["reports"])

MAX_RANGE_DAYS = 366


async def _load_report(
    sessions,
    user_id: int,
    session_uuid: uuid_module.UUID,
    fig_weight_g: float | None,
    count_source: Literal["user", "model"] = "model",
):
    scan = await sessions.get(user_id, session_uuid)
    if scan is None:
        raise NotFound("Session not found")

    metrics = await sessions.fetch_metrics(user_id, session_uuid)
    if metrics is None:
        raise NotFound("Session not found")

    use_user_counts = count_source == "user"
    return build_session_report(
        batch_id=scan.batch_id,
        device_label=scan.device_label,
        started_at=scan.start_time,
        ended_at=scan.end_time,
        conf_threshold_used=scan.conf_threshold,
        metrics=metrics,
        now=datetime.now(UTC),
        fig_weight_g=fig_weight_g if fig_weight_g is not None else scan.fig_weight_g,
        total_count_override=scan.effective_total_count if use_user_counts else None,
        defect_count_override=scan.effective_defect_count if use_user_counts else None,
        count_source=count_source,
        manual_counts_applied=use_user_counts and scan.is_manually_corrected,
    )


@router.get("/sessions/{session_uuid}/report", response_model=SessionReportResponse)
async def session_report(
    session_uuid: uuid_module.UUID,
    user: CurrentUser,
    sessions: SessionRepositoryDep,
    fig_weight_g: float | None = Query(
        default=None,
        gt=0,
        le=1000,
        description=(
            "Optional average fig weight override in grams. When omitted, the weight stored "
            "for the session is used."
        ),
    ),
) -> SessionReportResponse:
    """Full report for one batch: throughput, per-class breakdown and model statistics."""
    report = await _load_report(sessions, user.id, session_uuid, fig_weight_g)
    return SessionReportResponse.from_report(report)


@router.get("/sessions/{session_uuid}/report.pdf", response_class=Response)
async def session_report_pdf(
    session_uuid: uuid_module.UUID,
    user: CurrentUser,
    sessions: SessionRepositoryDep,
    fig_weight_g: float | None = Query(default=None, gt=0, le=1000),
    source: Literal["user", "model"] = Query(
        default="user",
        description="Use user-corrected totals or the detector's raw model totals.",
    ),
) -> Response:
    """Download a Turkish PDF using either user-entered or raw model counts."""
    report = await _load_report(
        sessions,
        user.id,
        session_uuid,
        fig_weight_g,
        count_source=source,
    )

    # Rendering is CPU-bound and synchronous. On the event loop it would stall every other
    # connected farmer's frames for the duration.
    pdf = await anyio.to_thread.run_sync(render_session_report, report)

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{report.batch_id}_raporu.pdf"',
            "Content-Length": str(len(pdf)),
            "Cache-Control": "no-store",
        },
    )


@router.get("/reports/range", response_model=RangeReportResponse)
async def range_report(
    user: CurrentUser,
    sessions: SessionRepositoryDep,
    settings: AppSettings,
    start: Annotated[
        datetime | None,
        Query(description="Inclusive, ISO-8601. Defaults to 30 days before end."),
    ] = None,
    end: Annotated[
        datetime | None, Query(description="Inclusive, ISO-8601. Defaults to now.")
    ] = None,
) -> RangeReportResponse:
    """Totals across every session started in a window.

    Aggregated in SQL rather than by loading rows: unlike the per-batch percentiles this is
    plain counting, and a season could span thousands of sessions.
    """
    end = end or datetime.now(UTC)
    start = start or (end - timedelta(days=30))

    start, end = _normalise(start), _normalise(end)

    if start > end:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "start must not be after end")

    if (end - start) > timedelta(days=MAX_RANGE_DAYS):
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            f"Range must not exceed {MAX_RANGE_DAYS} days",
        )

    totals = await sessions.totals_between(user.id, start, end)

    return RangeReportResponse.from_totals(totals, start=start, end=end)


def _normalise(moment: datetime) -> datetime:
    """Treat a naive timestamp as UTC.

    Query parameters routinely arrive without an offset, and comparing a naive value against a
    TIMESTAMPTZ column raises on PostgreSQL.
    """
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
