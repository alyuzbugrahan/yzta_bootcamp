"""Sample-and-time gates for confirmation and cooldown.

The desktop pipeline counted frames (``stability_required``, ``PRESENCE_CONFIRM_FRAMES``,
``COOLDOWN_FRAMES``) against a local camera pinned at 30 fps. A browser client streaming over
a network sustains 5-10 fps, and neither naive conversion survives that:

* Keeping the frame counts stretches every gate by 3-6x in wall-clock time. The 267 ms
  cooldown becomes ~1 s.
* Converting to pure durations collapses the counts. 70 ms at 8 fps is less than one frame
  interval, so a single sample satisfies the gate and the anti-flicker filter that
  ``stability_required`` exists to provide disappears.

A :class:`Gate` therefore carries both floors and opens only when both are met. At 30 fps the
duration floor binds and behaviour matches the desktop app; at 8 fps the sample floor binds and
the gate takes longer in wall-clock terms but still sees the same amount of evidence. Trading
latency for evidence is the correct direction: the alternative is counting figs the model only
glimpsed once.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Gate:
    """A threshold requiring both a minimum sample count and a minimum elapsed time."""

    min_samples: int
    min_seconds: float

    def __post_init__(self) -> None:
        if self.min_samples < 1:
            raise ValueError("min_samples must be >= 1")
        if self.min_seconds < 0:
            raise ValueError("min_seconds must be >= 0")

    def is_open(self, samples: int, elapsed_seconds: float) -> bool:
        return samples >= self.min_samples and elapsed_seconds >= self.min_seconds


@dataclass(frozen=True, slots=True)
class Timings:
    """The four gates the pipeline needs, plus the IoU thresholds they pair with."""

    confirm: Gate
    lost: Gate
    presence: Gate
    cooldown: Gate
    track_iou_threshold: float
    slot_iou_threshold: float

    @classmethod
    def from_settings(cls, timing) -> Timings:  # app.config.TimingSettings
        return cls(
            confirm=Gate(timing.confirm_samples, timing.confirm_seconds),
            lost=Gate(timing.lost_samples, timing.lost_seconds),
            presence=Gate(timing.presence_samples, timing.presence_seconds),
            cooldown=Gate(timing.cooldown_samples, timing.cooldown_seconds),
            track_iou_threshold=timing.track_iou_threshold,
            slot_iou_threshold=timing.slot_iou_threshold,
        )
