"""One physical fig must produce exactly one record.

Double counting is the failure that matters commercially here: it inflates both the fig count
and the defect ratio a farmer is sorting on.
"""

from __future__ import annotations

from app.domain.gating import Gate
from app.domain.slots import SlotTracker

from .conftest import detection


def make(presence: Gate | None = None, cooldown: Gate | None = None, iou: float = 0.25):
    """Desktop-equivalent gates: PRESENCE_CONFIRM_FRAMES=3, COOLDOWN_FRAMES=8 at 30 fps."""
    return SlotTracker(
        presence=presence or Gate(3, 0.10),
        cooldown=cooldown or Gate(8, 0.27),
        iou_threshold=iou,
    )


def confirm_one_fig(tracker: SlotTracker, clock, det=None) -> list:
    """Feed the same detection until its slot locks. Returns the emitted inspections."""
    det = det or detection()
    emitted = []
    for _ in range(3):
        emitted += tracker.process([det], clock.now, latency_ms=50.0)
        clock.advance(0.05)
    return emitted


def test_locks_only_after_presence_gate(clock):
    tracker = make()
    det = detection()

    assert tracker.process([det], clock.now, 50.0) == []
    assert tracker.process([det], clock.advance(0.05), 50.0) == []

    emitted = tracker.process([det], clock.advance(0.05), 50.0)

    assert len(emitted) == 1
    assert emitted[0].decision == "Healthy"


def test_stationary_fig_is_recorded_exactly_once(clock):
    tracker = make()
    det = detection()

    total = confirm_one_fig(tracker, clock, det)

    # Keep it in view far beyond the gate.
    for _ in range(20):
        total += tracker.process([det], clock.advance(0.05), 50.0)

    assert len(total) == 1


def test_unlocked_slot_disappears_without_recording(clock):
    """A fig glimpsed for one frame is noise, not a fig."""
    tracker = make()

    tracker.process([detection()], clock.now, 50.0)
    emitted = tracker.process([], clock.advance(0.05), 50.0)

    assert emitted == []
    assert tracker.active_count == 0


def test_brief_dropout_does_not_re_record_the_same_fig(clock):
    """The fig is still on the belt; the model just missed it for two frames."""
    tracker = make()
    det = detection()

    total = confirm_one_fig(tracker, clock, det)

    total += tracker.process([], clock.advance(0.05), 50.0)
    total += tracker.process([], clock.advance(0.05), 50.0)
    for _ in range(3):
        total += tracker.process([det], clock.advance(0.05), 50.0)

    assert len(total) == 1, "same fig recorded twice after a short dropout"


def test_new_fig_after_cooldown_is_recorded_separately(clock):
    tracker = make()
    det = detection()

    total = confirm_one_fig(tracker, clock, det)
    assert len(total) == 1

    # Fig leaves for long enough that the slot is released.
    for _ in range(8):
        tracker.process([], clock.advance(0.05), 50.0)
    assert tracker.active_count == 0

    total += confirm_one_fig(tracker, clock, det)

    assert len(total) == 2, "next fig in the same position was not counted"


def test_two_figs_in_view_lock_independently(clock):
    tracker = make()
    left = detection(bbox=(0.05, 0.05, 0.25, 0.25))
    right = detection("Aflatoxin", bbox=(0.70, 0.70, 0.95, 0.95))

    emitted = []
    for _ in range(3):
        emitted += tracker.process([left, right], clock.now, 50.0)
        clock.advance(0.05)

    assert len(emitted) == 2
    assert {result.decision for result in emitted} == {"Healthy", "Aflatoxin"}


def test_counts_are_reported(clock):
    tracker = make()
    det = detection()

    confirm_one_fig(tracker, clock, det)

    assert tracker.active_count == 1
    assert tracker.locked_count == 1


def test_latency_is_carried_onto_the_record(clock):
    tracker = make()
    det = detection()

    emitted = []
    for _ in range(3):
        emitted += tracker.process([det], clock.now, latency_ms=87.65)
        clock.advance(0.05)

    assert emitted[0].latency_ms == 87.7
