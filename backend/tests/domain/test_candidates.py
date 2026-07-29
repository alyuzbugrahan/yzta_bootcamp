"""Candidate finding, including the resolution-independence fix.

The desktop config filtered on ``min_candidate_area_px = 2500``, calibrated for the rig's
1280x720 capture. Browser clients send arbitrary resolutions, so that filter is replaced by the
area *ratio* — ``test_same_scene_survives_downscaling`` is the regression that would have
caught the original behaviour.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.domain.candidates import (
    CandidateParams,
    box_to_normalized_frame,
    crop_and_square,
    find_fig_candidates,
)
from app.domain.models import Rect


def scene(width: int, height: int, *, fig_scale: float = 1.0) -> np.ndarray:
    """A dark frame with one bright fig-shaped ellipse, scaled with the frame."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    axes = (int(width * 0.0625 * fig_scale), int(height * 0.0625 * fig_scale))
    cv2.ellipse(
        frame,
        center=(width // 2, height // 2),
        axes=axes,
        angle=0,
        startAngle=0,
        endAngle=360,
        color=(220, 220, 220),
        thickness=-1,
    )
    return frame


@pytest.fixture
def params() -> CandidateParams:
    return CandidateParams()


def test_finds_a_fig_shaped_blob(params):
    found = find_fig_candidates(scene(640, 480), params)

    assert len(found) == 1
    rect = found[0]
    # Centre of the returned (padded) rect should sit near the ellipse centre.
    assert abs((rect.x + rect.w / 2) - 320) < 15
    assert abs((rect.y + rect.h / 2) - 240) < 15


def test_ignores_empty_frame(params):
    assert find_fig_candidates(np.zeros((480, 640, 3), dtype=np.uint8), params) == []


def test_rejects_speck_below_area_ratio(params):
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.circle(frame, (320, 240), 5, (255, 255, 255), -1)

    assert find_fig_candidates(frame, params) == []


def test_rejects_elongated_shape(params):
    """Cables and tray edges are large enough but far outside the fig aspect range."""
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(frame, (100, 234), (500, 246), (255, 255, 255), -1)

    assert find_fig_candidates(frame, params) == []


def test_rejects_diagonal_streak_by_fill_ratio(params):
    """A diagonal cable has a square bounding box, so only the fill filter catches it.

    Its aspect ratio is 1.0 and its area is well inside range; it is rejected solely because
    the contour fills ~5% of its bounding box. This is the case the fill threshold exists for.

    Note a ring is *not* rejected here: ``RETR_EXTERNAL`` returns its outer boundary, so
    ``contourArea`` measures the filled disc. That is inherited desktop behaviour, and such
    shapes are left for the model to reject.
    """
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.line(frame, (150, 90), (450, 390), (255, 255, 255), thickness=12)

    assert find_fig_candidates(frame, params) == []


@pytest.mark.parametrize(
    ("width", "height"),
    [(1280, 720), (960, 540), (640, 360), (480, 270)],
)
def test_same_scene_survives_downscaling(params, width, height):
    """The same physical fig must be found at every resolution a browser might send.

    Under the desktop's absolute 2500 px threshold the 640x360 and 480x270 cases would find
    nothing, and nothing anywhere would report an error.
    """
    found = find_fig_candidates(scene(width, height), params)

    assert len(found) == 1, f"fig lost at {width}x{height}"


def test_crop_is_square_and_resized():
    frame = scene(640, 480)
    crop = crop_and_square(frame, Rect(x=100, y=100, w=80, h=40), input_size=640)

    assert crop.image.shape == (640, 640, 3)
    assert crop.square_edge == 80
    assert crop.pad_top == 20
    assert crop.pad_left == 0


def test_box_projects_back_to_source_frame():
    """A model-space box covering the whole crop maps back onto the padded source region."""
    crop = crop_and_square(scene(640, 480), Rect(x=200, y=100, w=100, h=100), input_size=640)

    bbox = box_to_normalized_frame([0, 0, 640, 640], crop, 640, 640, 480)

    assert bbox[0] == pytest.approx(200 / 640, abs=1e-3)
    assert bbox[1] == pytest.approx(100 / 480, abs=1e-3)
    assert bbox[2] == pytest.approx(300 / 640, abs=1e-3)
    assert bbox[3] == pytest.approx(200 / 480, abs=1e-3)


def test_projected_box_is_clamped_to_unit_range():
    crop = crop_and_square(scene(640, 480), Rect(x=600, y=440, w=40, h=40), input_size=640)

    bbox = box_to_normalized_frame([0, 0, 1280, 1280], crop, 640, 640, 480)

    assert 0.0 <= bbox[0] <= 1.0
    assert bbox[2] <= 1.0
    assert bbox[3] <= 1.0
