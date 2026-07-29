from __future__ import annotations

from app.domain.gating import Gate
from app.domain.stabilizer import TemporalStabilizer

from .conftest import detection


def make(confirm: Gate | None = None, lost: Gate | None = None, iou: float = 0.35):
    """Desktop-equivalent gates: stability_required=2, max_missing_frames=3 at 30 fps."""
    return TemporalStabilizer(
        confirm=confirm or Gate(2, 0.07),
        lost=lost or Gate(3, 0.10),
        iou_threshold=iou,
    )


def test_first_sighting_is_withheld(clock):
    stabilizer = make()

    assert stabilizer.apply([detection()], clock.now) == []


def test_emits_once_both_floors_are_met(clock):
    stabilizer = make()

    assert stabilizer.apply([detection()], clock.now) == []
    accepted = stabilizer.apply([detection()], clock.advance(0.10))

    assert len(accepted) == 1


def test_withholds_when_samples_arrive_too_fast(clock):
    """Two sightings 10 ms apart are two frames of a 100 fps burst, not 70 ms of evidence."""
    stabilizer = make()

    stabilizer.apply([detection()], clock.now)

    assert stabilizer.apply([detection()], clock.advance(0.01)) == []


def test_class_flip_restarts_confirmation(clock):
    """A region that changes class must serve the full gate again rather than inherit hits.

    Without this, one frame of Aflatoxin over a confirmed Healthy fig would be emitted
    immediately and recorded as a defect.
    """
    stabilizer = make()

    stabilizer.apply([detection("Healthy")], clock.now)
    stabilizer.apply([detection("Healthy")], clock.advance(0.10))

    flipped = stabilizer.apply([detection("Aflatoxin")], clock.advance(0.10))

    assert flipped == []


def test_track_survives_a_single_dropped_frame(clock):
    """One missed detection must not reset a confirmed track — figs flicker under UV."""
    stabilizer = make()

    stabilizer.apply([detection()], clock.now)
    stabilizer.apply([detection()], clock.advance(0.10))

    stabilizer.apply([], clock.advance(0.05))
    accepted = stabilizer.apply([detection()], clock.advance(0.05))

    assert len(accepted) == 1, "track was dropped after a single miss"


def test_track_is_dropped_once_lost_gate_opens(clock):
    stabilizer = make()

    stabilizer.apply([detection()], clock.now)
    stabilizer.apply([detection()], clock.advance(0.10))
    assert stabilizer.track_count == 1

    for _ in range(3):
        stabilizer.apply([], clock.advance(0.06))

    assert stabilizer.track_count == 0


def test_reappearance_after_loss_needs_reconfirmation(clock):
    stabilizer = make()

    stabilizer.apply([detection()], clock.now)
    stabilizer.apply([detection()], clock.advance(0.10))

    for _ in range(3):
        stabilizer.apply([], clock.advance(0.06))

    assert stabilizer.apply([detection()], clock.advance(0.06)) == []


def test_distant_detection_starts_a_separate_track(clock):
    """Two figs far apart must not reinforce each other's counters."""
    stabilizer = make()

    left = detection(bbox=(0.05, 0.05, 0.25, 0.25))
    right = detection(bbox=(0.70, 0.70, 0.95, 0.95))

    stabilizer.apply([left], clock.now)
    accepted = stabilizer.apply([right], clock.advance(0.10))

    assert accepted == []
    assert stabilizer.track_count == 2


def test_two_stabilizers_do_not_share_state(clock):
    """The isolation the desktop engine lacked: tracks belong to a connection, not a process."""
    a, b = make(), make()

    a.apply([detection()], clock.now)
    a.apply([detection()], clock.advance(0.10))

    assert b.apply([detection()], clock.now) == []
    assert b.track_count == 1
