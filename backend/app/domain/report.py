"""Batch report computation.

Turns the raw inspection rows of a session into the figures a farmer needs: how many figs went
past the camera, how many were contaminated, how many were sound, and how much the model's own
numbers should be trusted.

Pure functions over plain values — no database, no framework — so every statistic is testable
without a server. The percentile and histogram work is done here rather than in SQL because
SQLite has no percentile function, and keeping the queries dialect-agnostic has already caught
one portability bug in this codebase (``SUM(decision = 'Aflatoxin')``). The cost is loading a
session's rows into memory; at a few tens of thousands of figs that is some hundreds of
kilobytes, and a single conveyor batch does not get larger than that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.domain.models import DECISION_AFLATOXIN, DECISION_HEALTHY

# Figs the model classified but not confidently. Not an error rate — a worklist. The desktop
# app surfaced confidence per fig and left the judgement to the operator; a batch report has to
# make the same information actionable without scrolling a table.
LOW_CONFIDENCE_THRESHOLD = 0.70

# Confidence histogram edges. Deliberately coarse: the point is "is the model sure about this
# batch", not a research-grade distribution.
HISTOGRAM_EDGES: tuple[float, ...] = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


@dataclass(frozen=True, slots=True)
class InspectionMetric:
    """The three columns a report needs from one inspection row."""

    decision: str
    confidence: float
    latency_ms: float


@dataclass(frozen=True, slots=True)
class Bucket:
    lower: float
    upper: float
    count: int

    @property
    def label(self) -> str:
        return f"{self.lower:.0%}-{self.upper:.0%}"


@dataclass(frozen=True, slots=True)
class ClassBreakdown:
    """Per-class counts and confidence, for the two decisions the model can return."""

    decision: str
    count: int
    share_pct: float
    mean_confidence: float
    min_confidence: float


@dataclass(frozen=True, slots=True)
class ModelAnalysis:
    """What the model itself reports about how it did.

    Statistics derived from the stored predictions — no second model and no external service is
    involved. ``low_confidence_count`` is the number worth a human recheck.
    """

    mean_confidence: float
    median_confidence: float
    low_confidence_count: int
    low_confidence_pct: float
    low_confidence_threshold: float
    confidence_histogram: list[Bucket]
    per_class: list[ClassBreakdown]
    latency_p50_ms: float
    latency_p95_ms: float
    latency_max_ms: float
    conf_threshold_used: float


@dataclass(frozen=True, slots=True)
class Throughput:
    """How much product went past the camera."""

    total_figs: int
    healthy_count: int
    aflatoxin_count: int
    defect_rate_pct: float
    duration_seconds: float
    figs_per_minute: float
    estimated_mass_g: float | None = None


@dataclass(frozen=True, slots=True)
class SessionReport:
    batch_id: str
    device_label: str | None
    started_at: datetime
    ended_at: datetime | None
    is_open: bool
    throughput: Throughput
    analysis: ModelAnalysis
    count_source: str = "model"
    manual_counts_applied: bool = False
    fig_weight_g: float | None = None
    notes: list[str] = field(default_factory=list)


def as_utc(moment: datetime) -> datetime:
    """Attach UTC to a naive timestamp.

    Storage dialects disagree: PostgreSQL returns ``TIMESTAMPTZ`` columns as aware datetimes,
    SQLite has no timestamp type at all and hands back whatever it stored — naive. Subtracting
    one from an aware ``now`` raises, so every timestamp entering a calculation is normalised
    here rather than relying on the backend in use.
    """
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def percentile(values: list[float], fraction: float) -> float:
    """Linear-interpolated percentile over a sorted copy.

    Interpolating rather than picking the nearest rank matters for the small batches a farmer
    actually runs: with nine figs, a nearest-rank p95 is just the maximum.
    """
    if not values:
        return 0.0

    ordered = sorted(values)

    if len(ordered) == 1:
        return round(ordered[0], 1)

    position = fraction * (len(ordered) - 1)
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    weight = position - lower_index

    return round(ordered[lower_index] * (1 - weight) + ordered[upper_index] * weight, 1)


def histogram(values: list[float], edges: tuple[float, ...] = HISTOGRAM_EDGES) -> list[Bucket]:
    """Bucket confidences. The final bucket includes its upper edge, so 1.0 is counted."""
    buckets: list[Bucket] = []

    for index in range(len(edges) - 1):
        lower, upper = edges[index], edges[index + 1]
        is_last = index == len(edges) - 2

        if is_last:
            count = sum(1 for value in values if lower <= value <= upper)
        else:
            count = sum(1 for value in values if lower <= value < upper)

        buckets.append(Bucket(lower=lower, upper=upper, count=count))

    return buckets


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _median(values: list[float]) -> float:
    return round(percentile(values, 0.5), 4) if values else 0.0


def build_analysis(
    metrics: list[InspectionMetric],
    conf_threshold_used: float,
    low_confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD,
) -> ModelAnalysis:
    confidences = [m.confidence for m in metrics]
    latencies = [m.latency_ms for m in metrics]
    total = len(metrics)

    low_confidence = [c for c in confidences if c < low_confidence_threshold]

    per_class: list[ClassBreakdown] = []
    for decision in (DECISION_AFLATOXIN, DECISION_HEALTHY):
        subset = [m.confidence for m in metrics if m.decision == decision]
        per_class.append(
            ClassBreakdown(
                decision=decision,
                count=len(subset),
                share_pct=round(len(subset) / total * 100, 2) if total else 0.0,
                mean_confidence=_mean(subset),
                min_confidence=round(min(subset), 4) if subset else 0.0,
            )
        )

    return ModelAnalysis(
        mean_confidence=_mean(confidences),
        median_confidence=_median(confidences),
        low_confidence_count=len(low_confidence),
        low_confidence_pct=round(len(low_confidence) / total * 100, 2) if total else 0.0,
        low_confidence_threshold=low_confidence_threshold,
        confidence_histogram=histogram(confidences),
        per_class=per_class,
        latency_p50_ms=percentile(latencies, 0.50),
        latency_p95_ms=percentile(latencies, 0.95),
        latency_max_ms=round(max(latencies), 1) if latencies else 0.0,
        conf_threshold_used=conf_threshold_used,
    )


def build_throughput(
    metrics: list[InspectionMetric],
    started_at: datetime,
    ended_at: datetime | None,
    now: datetime,
    fig_weight_g: float | None = None,
    total_count_override: int | None = None,
    defect_count_override: int | None = None,
) -> Throughput:
    detected_total = len(metrics)
    detected_aflatoxin = sum(1 for m in metrics if m.decision == DECISION_AFLATOXIN)
    total = total_count_override if total_count_override is not None else detected_total
    aflatoxin = (
        defect_count_override if defect_count_override is not None else detected_aflatoxin
    )
    healthy = max(total - aflatoxin, 0)

    # An open session is measured to now, so a live report shows a meaningful rate rather than
    # dividing by zero.
    finished = as_utc(ended_at or now)
    duration = max((finished - as_utc(started_at)).total_seconds(), 0.0)

    return Throughput(
        total_figs=total,
        healthy_count=healthy,
        aflatoxin_count=aflatoxin,
        defect_rate_pct=round(aflatoxin / total * 100, 2) if total else 0.0,
        duration_seconds=round(duration, 1),
        figs_per_minute=round(total / (duration / 60), 2) if duration >= 1.0 else 0.0,
        estimated_mass_g=round(total * fig_weight_g, 1) if fig_weight_g else None,
    )


def build_notes(throughput: Throughput, analysis: ModelAnalysis) -> list[str]:
    """Plain-language flags a farmer should act on.

    Deliberately mechanical — fixed thresholds over the computed statistics, not a generated
    narrative. Each line states what was observed and what to do; none of them claim a cause.
    """
    notes: list[str] = []

    if throughput.total_figs == 0:
        notes.append("No figs were recorded in this session.")
        return notes

    if analysis.low_confidence_pct >= 20:
        notes.append(
            f"{analysis.low_confidence_pct:.0f}% of figs scored below "
            f"{analysis.low_confidence_threshold:.0%} confidence. Check UV lighting and camera "
            f"focus, and recheck those figs by hand before trusting this batch."
        )
    elif analysis.low_confidence_count:
        notes.append(
            f"{analysis.low_confidence_count} fig(s) scored below "
            f"{analysis.low_confidence_threshold:.0%} confidence and are worth a manual look."
        )

    if throughput.defect_rate_pct >= 20:
        notes.append(
            f"Contamination rate is {throughput.defect_rate_pct:.1f}%, which is high. "
            f"Consider re-sorting this batch."
        )

    if throughput.total_figs < 30:
        notes.append(
            f"Only {throughput.total_figs} figs were scanned; percentages from a batch this "
            f"small vary a lot and should not be read as a rate for the whole harvest."
        )

    return notes


def build_session_report(
    *,
    batch_id: str,
    device_label: str | None,
    started_at: datetime,
    ended_at: datetime | None,
    conf_threshold_used: float,
    metrics: list[InspectionMetric],
    now: datetime,
    fig_weight_g: float | None = None,
    total_count_override: int | None = None,
    defect_count_override: int | None = None,
    count_source: str = "model",
    manual_counts_applied: bool = False,
) -> SessionReport:
    throughput = build_throughput(
        metrics,
        started_at,
        ended_at,
        now,
        fig_weight_g,
        total_count_override=total_count_override,
        defect_count_override=defect_count_override,
    )
    analysis = build_analysis(metrics, conf_threshold_used)

    return SessionReport(
        batch_id=batch_id,
        device_label=device_label,
        started_at=as_utc(started_at),
        ended_at=as_utc(ended_at) if ended_at else None,
        is_open=ended_at is None,
        throughput=throughput,
        analysis=analysis,
        count_source=count_source,
        manual_counts_applied=manual_counts_applied,
        fig_weight_g=fig_weight_g,
        notes=build_notes(throughput, analysis),
    )
