"""Batch report statistics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.report import (
    HISTOGRAM_EDGES,
    InspectionMetric,
    build_analysis,
    build_notes,
    build_session_report,
    build_throughput,
    histogram,
    percentile,
)

START = datetime(2026, 7, 29, 9, 0, 0, tzinfo=UTC)


def metric(decision="Healthy", confidence=0.9, latency=50.0) -> InspectionMetric:
    return InspectionMetric(decision=decision, confidence=confidence, latency_ms=latency)


def batch(healthy: int, aflatoxin: int, confidence: float = 0.9) -> list[InspectionMetric]:
    return [metric("Healthy", confidence) for _ in range(healthy)] + [
        metric("Aflatoxin", confidence) for _ in range(aflatoxin)
    ]


# ── Percentiles ───────────────────────────────────────────────────────────


def test_percentile_of_empty_is_zero():
    assert percentile([], 0.5) == 0.0


def test_percentile_of_single_value():
    assert percentile([42.0], 0.95) == 42.0


def test_median():
    assert percentile([1.0, 2.0, 3.0], 0.5) == 2.0


def test_percentile_interpolates():
    """Nearest-rank would return the maximum for p95 of a small batch, which is misleading
    when a farmer has scanned nine figs."""
    values = [float(v) for v in range(1, 10)]

    assert percentile(values, 0.95) == pytest.approx(8.6, abs=0.05)


def test_percentile_is_order_independent():
    assert percentile([9.0, 1.0, 5.0], 0.5) == percentile([1.0, 5.0, 9.0], 0.5)


# ── Histogram ─────────────────────────────────────────────────────────────


def test_histogram_covers_every_edge_pair():
    assert len(histogram([])) == len(HISTOGRAM_EDGES) - 1


def test_histogram_counts_every_value_exactly_once():
    values = [0.1, 0.55, 0.65, 0.72, 0.85, 0.93, 1.0]

    buckets = histogram(values)

    assert sum(b.count for b in buckets) == len(values)


def test_perfect_confidence_lands_in_the_last_bucket():
    """1.0 is a real score; a half-open final bucket would silently discard it."""
    buckets = histogram([1.0])

    assert buckets[-1].count == 1


def test_bucket_labels_are_percentages():
    assert histogram([])[0].label == "0%-50%"


# ── Model analysis ────────────────────────────────────────────────────────


def test_analysis_of_empty_session_is_all_zero():
    analysis = build_analysis([], conf_threshold_used=0.5)

    assert analysis.mean_confidence == 0.0
    assert analysis.low_confidence_count == 0
    assert analysis.low_confidence_pct == 0.0


def test_mean_and_median_confidence():
    metrics = [metric(confidence=c) for c in (0.6, 0.8, 1.0)]

    analysis = build_analysis(metrics, conf_threshold_used=0.5)

    assert analysis.mean_confidence == pytest.approx(0.8)
    assert analysis.median_confidence == pytest.approx(0.8)


def test_low_confidence_figs_are_counted():
    """The worklist: figs the model classified but was not sure about."""
    metrics = [metric(confidence=c) for c in (0.55, 0.65, 0.75, 0.95)]

    analysis = build_analysis(metrics, conf_threshold_used=0.5)

    assert analysis.low_confidence_count == 2
    assert analysis.low_confidence_pct == 50.0


def test_per_class_breakdown():
    analysis = build_analysis(batch(healthy=7, aflatoxin=3), conf_threshold_used=0.5)

    by_decision = {c.decision: c for c in analysis.per_class}

    assert by_decision["Healthy"].count == 7
    assert by_decision["Aflatoxin"].count == 3
    assert by_decision["Aflatoxin"].share_pct == 30.0


def test_both_classes_are_reported_even_when_absent():
    """A batch with no contamination must still show Aflatoxin as zero, not omit the row."""
    analysis = build_analysis(batch(healthy=5, aflatoxin=0), conf_threshold_used=0.5)

    by_decision = {c.decision: c for c in analysis.per_class}

    assert by_decision["Aflatoxin"].count == 0
    assert by_decision["Aflatoxin"].mean_confidence == 0.0


def test_latency_percentiles():
    metrics = [metric(latency=float(v)) for v in range(1, 101)]

    analysis = build_analysis(metrics, conf_threshold_used=0.5)

    assert analysis.latency_p50_ms == pytest.approx(50.5, abs=0.6)
    assert analysis.latency_max_ms == 100.0


def test_threshold_used_is_carried_through():
    """The report has to state the threshold the session ran at; the same figs scored against
    a different threshold are not comparable."""
    analysis = build_analysis(batch(1, 0), conf_threshold_used=0.73)

    assert analysis.conf_threshold_used == 0.73


# ── Throughput ────────────────────────────────────────────────────────────


def test_throughput_counts():
    result = build_throughput(
        batch(healthy=8, aflatoxin=2), START, START + timedelta(minutes=1), now=START
    )

    assert result.total_figs == 10
    assert result.healthy_count == 8
    assert result.aflatoxin_count == 2
    assert result.defect_rate_pct == 20.0


def test_empty_session_does_not_divide_by_zero():
    result = build_throughput([], START, START, now=START)

    assert result.total_figs == 0
    assert result.defect_rate_pct == 0.0
    assert result.figs_per_minute == 0.0


def test_figs_per_minute():
    result = build_throughput(
        batch(healthy=120, aflatoxin=0), START, START + timedelta(minutes=2), now=START
    )

    assert result.figs_per_minute == 60.0


def test_open_session_is_measured_to_now():
    """A live report must show a rate, not divide by a duration of zero."""
    result = build_throughput(
        batch(healthy=60, aflatoxin=0),
        START,
        ended_at=None,
        now=START + timedelta(minutes=1),
    )

    assert result.figs_per_minute == 60.0


def test_very_short_session_reports_no_rate():
    """Under a second, figs-per-minute is noise amplified by 60."""
    result = build_throughput(batch(5, 0), START, START + timedelta(milliseconds=200), START)

    assert result.figs_per_minute == 0.0


def test_estimated_mass_when_fig_weight_is_known():
    """The desktop app had a fig-weight field that was never persisted (main_window.py:287)."""
    result = build_throughput(
        batch(healthy=100, aflatoxin=0), START, START + timedelta(minutes=1), START,
        fig_weight_g=10.0,
    )

    assert result.estimated_mass_g == 1000.0


def test_mass_is_omitted_when_weight_is_unknown():
    result = build_throughput(batch(10, 0), START, START, START)

    assert result.estimated_mass_g is None


# ── Notes ─────────────────────────────────────────────────────────────────


def test_empty_session_says_so_and_stops():
    notes = build_notes(
        build_throughput([], START, START, START), build_analysis([], 0.5)
    )

    assert len(notes) == 1
    assert "No figs" in notes[0]


def test_widespread_low_confidence_is_flagged():
    metrics = [metric(confidence=0.55) for _ in range(50)]

    notes = build_notes(
        build_throughput(metrics, START, START + timedelta(minutes=1), START),
        build_analysis(metrics, 0.5),
    )

    assert any("UV lighting" in note for note in notes)


def test_high_contamination_is_flagged():
    metrics = batch(healthy=50, aflatoxin=50)

    notes = build_notes(
        build_throughput(metrics, START, START + timedelta(minutes=1), START),
        build_analysis(metrics, 0.5),
    )

    assert any("re-sorting" in note for note in notes)


def test_small_batches_are_qualified():
    """A 100% defect rate over four figs is not a harvest-wide rate, and the report must say so
    rather than let the headline number be read as one."""
    metrics = batch(healthy=0, aflatoxin=4)

    notes = build_notes(
        build_throughput(metrics, START, START + timedelta(minutes=1), START),
        build_analysis(metrics, 0.5),
    )

    assert any("vary a lot" in note for note in notes)


def test_a_clean_confident_batch_raises_nothing():
    metrics = batch(healthy=95, aflatoxin=5, confidence=0.95)

    notes = build_notes(
        build_throughput(metrics, START, START + timedelta(minutes=1), START),
        build_analysis(metrics, 0.5),
    )

    assert notes == []


# ── Whole report ──────────────────────────────────────────────────────────


def test_session_report_assembles():
    report = build_session_report(
        batch_id="BATCH_20260729_090000",
        device_label="Barn cam",
        started_at=START,
        ended_at=START + timedelta(minutes=5),
        conf_threshold_used=0.6,
        metrics=batch(healthy=90, aflatoxin=10, confidence=0.92),
        now=START,
        fig_weight_g=10.0,
    )

    assert report.batch_id == "BATCH_20260729_090000"
    assert report.is_open is False
    assert report.throughput.total_figs == 100
    assert report.throughput.aflatoxin_count == 10
    assert report.throughput.defect_rate_pct == 10.0
    assert report.throughput.estimated_mass_g == 1000.0
    assert report.analysis.conf_threshold_used == 0.6
    assert sum(b.count for b in report.analysis.confidence_histogram) == 100



def test_session_report_can_use_user_count_overrides():
    report = build_session_report(
        batch_id="BATCH_MANUAL",
        device_label="Barn cam",
        started_at=START,
        ended_at=START + timedelta(minutes=5),
        conf_threshold_used=0.5,
        metrics=batch(healthy=8, aflatoxin=2),
        now=START,
        fig_weight_g=20.0,
        total_count_override=12,
        defect_count_override=3,
        count_source="user",
        manual_counts_applied=True,
    )

    assert report.throughput.total_figs == 12
    assert report.throughput.healthy_count == 9
    assert report.throughput.aflatoxin_count == 3
    assert report.throughput.estimated_mass_g == 240.0
    assert report.count_source == "user"
    assert report.manual_counts_applied is True

def test_open_session_report_is_marked_open():
    report = build_session_report(
        batch_id="BATCH_LIVE",
        device_label=None,
        started_at=START,
        ended_at=None,
        conf_threshold_used=0.5,
        metrics=batch(3, 1),
        now=START + timedelta(minutes=1),
    )

    assert report.is_open is True
