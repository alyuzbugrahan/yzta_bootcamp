"""Fig-candidate detection and crop geometry.

Ported from ``vision/inference_engine.py`` (``_find_figs``, ``_crop_and_square``,
``_box_to_original``) as pure functions taking explicit parameters.

This stage is what makes the pipeline affordable: when no fig-shaped contour is present the
model is never invoked at all (inference_engine.py:324). On a server shared between farmers
that gate is the main throughput lever, so its behaviour is preserved exactly — with one
deliberate exception, documented on :class:`CandidateParams`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from app.domain.models import Rect


@dataclass(frozen=True, slots=True)
class CandidateParams:
    """Contour filters.

    The desktop config also carried ``min_candidate_area_px = 2500``, an absolute pixel
    count. It is intentionally not reproduced: it was calibrated against the rig's fixed
    1280x720 capture, and browser clients send whatever their camera and downscaler produce.
    At 640x360 the same physical fig covers a quarter of the pixels and would be discarded
    before ever reaching the model, with no error anywhere. ``min_area_ratio`` expresses the
    same "too small to be a fig" intent independently of resolution.
    """

    min_area_ratio: float = 0.006
    max_area_ratio: float = 0.80
    padding_ratio: float = 0.08
    min_aspect: float = 0.35
    max_aspect: float = 2.85
    min_fill_ratio: float = 0.25

    @classmethod
    def from_settings(cls, vision) -> CandidateParams:  # app.config.VisionSettings
        return cls(
            min_area_ratio=vision.min_area_ratio,
            max_area_ratio=vision.max_area_ratio,
            padding_ratio=vision.padding_ratio,
            min_aspect=vision.min_aspect,
            max_aspect=vision.max_aspect,
            min_fill_ratio=vision.min_fill_ratio,
        )


@dataclass(frozen=True, slots=True)
class SquaredCrop:
    """A candidate crop padded to a square and resized to the model's input size.

    The padding offsets and the pre-resize square edge are retained so a box predicted in
    model space can be projected back onto the source frame.
    """

    image: np.ndarray
    source: Rect
    pad_left: int
    pad_top: int
    square_edge: int


def find_fig_candidates(frame: np.ndarray, params: CandidateParams) -> list[Rect]:
    """Locate regions that plausibly contain a fig.

    Otsu threshold on greyscale, morphological close then open, external contours filtered by
    area ratio, aspect ratio and fill ratio. Deliberately not HSV — the source is UV
    fluorescence imagery where hue carries little separable information.
    """
    frame_h, frame_w = frame.shape[:2]
    frame_area = float(frame_w * frame_h)

    if frame_area <= 0:
        return []

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    kernel = np.ones((5, 5), np.uint8)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: list[Rect] = []

    for contour in contours:
        area = cv2.contourArea(contour)
        area_ratio = area / frame_area

        if area_ratio < params.min_area_ratio or area_ratio > params.max_area_ratio:
            continue

        x, y, w, h = cv2.boundingRect(contour)

        if w <= 0 or h <= 0:
            continue

        aspect = w / float(h)
        if aspect < params.min_aspect or aspect > params.max_aspect:
            continue

        # Rejects wires, tray edges and fragmented noise, which fill their bounding box poorly.
        fill_ratio = area / float(w * h + 1e-6)
        if fill_ratio < params.min_fill_ratio:
            continue

        pad = int(max(w, h) * params.padding_ratio)

        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(frame_w, x + w + pad)
        y2 = min(frame_h, y + h + pad)

        candidates.append(Rect(x1, y1, x2 - x1, y2 - y1))

    return candidates


def crop_and_square(frame: np.ndarray, rect: Rect, input_size: int) -> SquaredCrop:
    """Crop the candidate, letterbox it to a square with black, resize to ``input_size``."""
    cropped = frame[rect.y : rect.y + rect.h, rect.x : rect.x + rect.w]

    square_edge = max(rect.w, rect.h)

    pad_top = (square_edge - rect.h) // 2
    pad_bottom = square_edge - rect.h - pad_top
    pad_left = (square_edge - rect.w) // 2
    pad_right = square_edge - rect.w - pad_left

    squared = cv2.copyMakeBorder(
        cropped,
        pad_top,
        pad_bottom,
        pad_left,
        pad_right,
        cv2.BORDER_CONSTANT,
        value=[0, 0, 0],
    )

    resized = cv2.resize(squared, (input_size, input_size), interpolation=cv2.INTER_AREA)

    return SquaredCrop(
        image=resized,
        source=rect,
        pad_left=pad_left,
        pad_top=pad_top,
        square_edge=square_edge,
    )


def box_to_normalized_frame(
    xyxy: Sequence[float],
    crop: SquaredCrop,
    input_size: int,
    frame_w: int,
    frame_h: int,
) -> tuple[float, float, float, float]:
    """Project a model-space box back onto the source frame, normalised to 0-1."""
    scale = crop.square_edge / float(input_size)

    abs_x1 = crop.source.x - crop.pad_left + xyxy[0] * scale
    abs_y1 = crop.source.y - crop.pad_top + xyxy[1] * scale
    abs_x2 = crop.source.x - crop.pad_left + xyxy[2] * scale
    abs_y2 = crop.source.y - crop.pad_top + xyxy[3] * scale

    return (
        max(0.0, abs_x1 / frame_w),
        max(0.0, abs_y1 / frame_h),
        min(1.0, abs_x2 / frame_w),
        min(1.0, abs_y2 / frame_h),
    )
