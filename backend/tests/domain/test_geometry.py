from __future__ import annotations

from app.domain.geometry import box_iou, nms_indices


def test_iou_identical_boxes_is_one():
    """Approximate, because the shared denominator carries the desktop code's 1e-6 guard."""
    box = [0.1, 0.1, 0.5, 0.5]
    assert box_iou(box, box) == pytest_approx(1.0, tol=1e-4)


def test_iou_disjoint_boxes_is_zero():
    assert box_iou([0.0, 0.0, 0.1, 0.1], [0.5, 0.5, 0.9, 0.9]) == 0.0


def test_iou_half_overlap():
    # Two unit squares sharing exactly half their area.
    assert box_iou([0.0, 0.0, 1.0, 1.0], [0.5, 0.0, 1.5, 1.0]) == pytest_approx(1 / 3)


def test_nms_keeps_highest_score_and_suppresses_overlap():
    boxes = [
        [0.0, 0.0, 1.0, 1.0],
        [0.05, 0.05, 1.05, 1.05],  # heavy overlap with the first
        [5.0, 5.0, 6.0, 6.0],  # disjoint
    ]
    scores = [0.8, 0.9, 0.7]

    keep = nms_indices(boxes, scores, iou_threshold=0.5)

    assert keep[0] == 1, "highest-scoring box must be kept first"
    assert 0 not in keep, "overlapping lower-scoring box must be suppressed"
    assert 2 in keep, "disjoint box must survive"


def test_nms_empty_input():
    assert nms_indices([], [], 0.5) == []


def test_nms_single_box():
    assert nms_indices([[0.0, 0.0, 1.0, 1.0]], [0.5], 0.5) == [0]


def test_suppression_follows_the_threshold():
    """Two boxes overlapping at IoU = 1/3, checked either side of that value.

    The exact boundary is not pinned: the 1e-6 guard in the denominator puts the computed
    value fractionally below 1/3, so asserting on equality would be testing float noise
    rather than the suppression rule.
    """
    boxes = [[0.0, 0.0, 1.0, 1.0], [0.5, 0.0, 1.5, 1.0]]
    scores = [0.9, 0.8]

    assert nms_indices(boxes, scores, iou_threshold=0.30) == [0], "should suppress"
    assert nms_indices(boxes, scores, iou_threshold=0.40) == [0, 1], "should keep both"


def pytest_approx(value: float, tol: float = 1e-6):
    import pytest

    return pytest.approx(value, abs=tol)
