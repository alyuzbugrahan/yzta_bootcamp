"""Request and response models.

These are the API contract the frontend codes against; the OpenAPI schema at ``/docs`` is
generated from them. Kept separate from the ORM models so a column rename is not automatically
a breaking API change.
"""

from __future__ import annotations

import uuid as uuid_module
from dataclasses import asdict
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.infra.repositories.session_repository import SessionSummary

# ── Auth ──────────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: EmailStr
    # Length is the only rule enforced. Composition requirements ("one symbol, one digit")
    # push people toward predictable substitutions without adding real entropy.
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    created_at: datetime


# ── Sessions ──────────────────────────────────────────────────────────────


class CreateSessionRequest(BaseModel):
    conf_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Overrides the server default for this session only.",
    )
    device_label: str | None = Field(default=None, max_length=120)


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: uuid_module.UUID
    batch_id: str
    device_label: str | None
    conf_threshold: float
    start_time: datetime
    end_time: datetime | None
    total_count: int
    defect_count: int
    is_open: bool


class SessionCreatedResponse(SessionResponse):
    ws_url: str = Field(description="WebSocket path to stream frames to (Phase 4).")


class TicketResponse(BaseModel):
    """Short-lived credential for the WebSocket handshake.

    A browser cannot set an Authorization header on a WebSocket, so the credential has to
    travel in the URL — where proxies log it and browsers keep it in history. This one expires
    in a minute and is bound to a single session, unlike the 15-minute access token.
    """

    ticket: str
    ws_url: str
    expires_in: int = Field(description="Ticket lifetime in seconds")


class SummaryResponse(BaseModel):
    total: int
    aflatoxin: int
    healthy: int
    ratio_pct: float
    avg_conf: float
    avg_lat_ms: float
    min_lat_ms: float
    max_lat_ms: float

    @classmethod
    def from_summary(cls, summary: SessionSummary) -> SummaryResponse:
        return cls(
            total=summary.total,
            aflatoxin=summary.aflatoxin,
            healthy=summary.healthy,
            ratio_pct=summary.ratio_pct,
            avg_conf=summary.avg_conf,
            avg_lat_ms=summary.avg_lat_ms,
            min_lat_ms=summary.min_lat_ms,
            max_lat_ms=summary.max_lat_ms,
        )


class SessionDetailResponse(BaseModel):
    session: SessionResponse
    summary: SummaryResponse


# ── Inspections ───────────────────────────────────────────────────────────


class InspectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    fig_seq: int
    timestamp: datetime
    decision: str
    confidence: float
    latency_ms: float
    image_key: str | None


# ── Pagination ────────────────────────────────────────────────────────────


class SessionPage(BaseModel):
    """Cursor-paginated, not offset-paginated.

    New sessions are inserted constantly while a farmer scrolls, and an OFFSET would shift rows
    between pages. ``next_cursor`` is null on the last page.
    """

    items: list[SessionResponse]
    next_cursor: int | None = None


class InspectionPage(BaseModel):
    items: list[InspectionResponse]
    next_cursor: int | None = None


# ── Reports ───────────────────────────────────────────────────────────────


class BucketResponse(BaseModel):
    label: str
    lower: float
    upper: float
    count: int


class ClassBreakdownResponse(BaseModel):
    decision: str
    count: int
    share_pct: float
    mean_confidence: float
    min_confidence: float


class ModelAnalysisResponse(BaseModel):
    """Statistics derived from the detector's own scores.

    No second model and no external service is involved — every figure here is computed from
    predictions already stored for this batch.
    """

    mean_confidence: float
    median_confidence: float
    low_confidence_count: int
    low_confidence_pct: float
    low_confidence_threshold: float
    confidence_histogram: list[BucketResponse]
    per_class: list[ClassBreakdownResponse]
    latency_p50_ms: float
    latency_p95_ms: float
    latency_max_ms: float
    conf_threshold_used: float


class ThroughputResponse(BaseModel):
    total_figs: int
    healthy_count: int
    aflatoxin_count: int
    defect_rate_pct: float
    duration_seconds: float
    figs_per_minute: float
    estimated_mass_g: float | None


class SessionReportResponse(BaseModel):
    batch_id: str
    device_label: str | None
    started_at: datetime
    ended_at: datetime | None
    is_open: bool
    throughput: ThroughputResponse
    analysis: ModelAnalysisResponse
    notes: list[str]

    @classmethod
    def from_report(cls, report) -> SessionReportResponse:  # app.domain.report.SessionReport
        return cls(
            batch_id=report.batch_id,
            device_label=report.device_label,
            started_at=report.started_at,
            ended_at=report.ended_at,
            is_open=report.is_open,
            # asdict, not __dict__: the domain dataclasses use slots=True and have no __dict__.
            throughput=ThroughputResponse(**asdict(report.throughput)),
            analysis=ModelAnalysisResponse(
                mean_confidence=report.analysis.mean_confidence,
                median_confidence=report.analysis.median_confidence,
                low_confidence_count=report.analysis.low_confidence_count,
                low_confidence_pct=report.analysis.low_confidence_pct,
                low_confidence_threshold=report.analysis.low_confidence_threshold,
                confidence_histogram=[
                    BucketResponse(
                        label=b.label, lower=b.lower, upper=b.upper, count=b.count
                    )
                    for b in report.analysis.confidence_histogram
                ],
                per_class=[
                    ClassBreakdownResponse(**asdict(c)) for c in report.analysis.per_class
                ],
                latency_p50_ms=report.analysis.latency_p50_ms,
                latency_p95_ms=report.analysis.latency_p95_ms,
                latency_max_ms=report.analysis.latency_max_ms,
                conf_threshold_used=report.analysis.conf_threshold_used,
            ),
            notes=list(report.notes),
        )


class RangeReportResponse(BaseModel):
    start: datetime
    end: datetime
    sessions: int
    total_figs: int
    healthy_count: int
    aflatoxin_count: int
    defect_rate_pct: float
    mean_confidence: float

    @classmethod
    def from_totals(cls, totals, start: datetime, end: datetime) -> RangeReportResponse:
        return cls(
            start=start,
            end=end,
            sessions=totals.sessions,
            total_figs=totals.total_figs,
            healthy_count=totals.healthy_count,
            aflatoxin_count=totals.aflatoxin_count,
            defect_rate_pct=totals.defect_rate_pct,
            mean_confidence=totals.mean_confidence,
        )
