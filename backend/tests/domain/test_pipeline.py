"""End-to-end domain behaviour, and the isolation guarantee the web version depends on."""

from __future__ import annotations

import numpy as np

from app.domain.demo import DemoDetector
from app.domain.pipeline import ScanPipeline

from .conftest import FakeClock, ScriptedDetector, detection


def test_confirmed_fig_flows_through_to_an_inspection(timings, blank_frame):
    clock = FakeClock()
    det = detection()
    pipeline = ScanPipeline(
        ScriptedDetector([[det]] * 10), timings, clock=lambda: clock.now
    )

    inspections = []
    for _ in range(6):
        outcome = pipeline.process(blank_frame, conf=0.5, iou=0.45)
        inspections += outcome.inspections
        clock.advance(0.05)

    assert len(inspections) == 1
    assert inspections[0].decision == "Healthy"


def test_unconfirmed_detection_produces_no_inspection(timings, blank_frame):
    clock = FakeClock()
    pipeline = ScanPipeline(
        ScriptedDetector([[detection()], []]), timings, clock=lambda: clock.now
    )

    first = pipeline.process(blank_frame, conf=0.5, iou=0.45)
    clock.advance(0.05)
    second = pipeline.process(blank_frame, conf=0.5, iou=0.45)

    assert first.inspections == []
    assert second.inspections == []


def test_confidence_is_passed_through_per_call(timings, blank_frame):
    """Each connection supplies its own threshold; none of it is stored on the detector.

    The desktop slider mutated shared engine state (``set_conf_threshold``), which on a
    server would have let one farmer change every other farmer's sensitivity.
    """
    detector = ScriptedDetector([[]])
    pipeline = ScanPipeline(detector, timings, clock=FakeClock())

    pipeline.process(blank_frame, conf=0.42, iou=0.45)
    pipeline.process(blank_frame, conf=0.91, iou=0.45)

    assert detector.seen_conf == [0.42, 0.91]


def test_two_pipelines_sharing_one_detector_keep_separate_state(timings, blank_frame):
    """The core multi-tenancy property: one model, independent tracking per connection."""
    detector = ScriptedDetector([[detection()]] * 20)

    clock_a, clock_b = FakeClock(), FakeClock()
    a = ScanPipeline(detector, timings, clock=lambda: clock_a.now)
    b = ScanPipeline(detector, timings, clock=lambda: clock_b.now)

    # Drive A to the point of recording a fig.
    a_inspections = []
    for _ in range(6):
        a_inspections += a.process(blank_frame, 0.5, 0.45).inspections
        clock_a.advance(0.05)

    assert len(a_inspections) == 1

    # B has seen a single frame and must still be waiting.
    assert b.process(blank_frame, 0.5, 0.45).inspections == []


def test_reset_clears_temporal_state(timings, blank_frame):
    clock = FakeClock()
    pipeline = ScanPipeline(
        ScriptedDetector([[detection()]] * 10), timings, clock=lambda: clock.now
    )

    pipeline.process(blank_frame, 0.5, 0.45)
    clock.advance(0.05)
    pipeline.reset()

    outcome = pipeline.process(blank_frame, 0.5, 0.45)

    assert outcome.detections == []
    assert outcome.stats.active_slots == 0


def test_stats_report_slot_occupancy(timings, blank_frame):
    clock = FakeClock()
    pipeline = ScanPipeline(
        ScriptedDetector([[detection()]] * 10), timings, clock=lambda: clock.now
    )

    outcome = None
    for _ in range(6):
        outcome = pipeline.process(blank_frame, 0.5, 0.45)
        clock.advance(0.05)

    assert outcome.stats.active_slots == 1
    assert outcome.stats.locked_slots == 1
    assert outcome.stats.latency_ms >= 0.0


def test_demo_detectors_are_independent_per_connection():
    """Demo RNG was instance state on the shared desktop engine; it must not be shared now."""
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    a = DemoDetector(seed=7)
    b = DemoDetector(seed=7)

    first = [len(a.predict(frame, 0.5, 0.45)) for _ in range(5)]
    second = [len(b.predict(frame, 0.5, 0.45)) for _ in range(5)]

    assert first == second, "same seed must give each connection the same sequence"
