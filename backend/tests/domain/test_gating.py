"""The both-floors gate is the mechanism that keeps web behaviour honest at low frame rates.

Each test here corresponds to a way the naive frame-count → duration conversion fails.
"""

from __future__ import annotations

import pytest

from app.domain.gating import Gate


def test_requires_both_floors():
    gate = Gate(min_samples=3, min_seconds=0.10)

    assert not gate.is_open(samples=3, elapsed_seconds=0.05), "time floor ignored"
    assert not gate.is_open(samples=2, elapsed_seconds=0.50), "sample floor ignored"
    assert gate.is_open(samples=3, elapsed_seconds=0.10)


def test_low_frame_rate_cannot_satisfy_gate_with_one_sample():
    """A single frame must never confirm, however long the interval before it was.

    This is the failure mode of converting frame counts to bare durations: at 8 fps the
    125 ms between frames already exceeds a 100 ms threshold, so one sighting would confirm
    a fig and the anti-flicker filter would be gone.
    """
    gate = Gate(min_samples=3, min_seconds=0.10)

    assert not gate.is_open(samples=1, elapsed_seconds=0.125)


def test_high_frame_rate_cannot_satisfy_gate_instantly():
    """Conversely, at 60 fps three samples arrive in 50 ms and must still wait.

    This is the failure mode of keeping bare frame counts: the gate would fire twice as fast
    as it did on the 30 fps rig.
    """
    gate = Gate(min_samples=3, min_seconds=0.10)

    assert not gate.is_open(samples=3, elapsed_seconds=0.05)
    assert gate.is_open(samples=3, elapsed_seconds=0.10)


@pytest.mark.parametrize("samples", [0, -1])
def test_rejects_invalid_sample_floor(samples):
    with pytest.raises(ValueError):
        Gate(min_samples=samples, min_seconds=0.1)


def test_rejects_negative_duration():
    with pytest.raises(ValueError):
        Gate(min_samples=1, min_seconds=-0.1)
