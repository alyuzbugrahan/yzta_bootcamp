"""Box arithmetic: IoU and non-maximum suppression.

The desktop engine carried two byte-identical NMS loops that differed only in the container
they iterated (``_nms`` over ``Detection``, ``_nms_raw_items`` over dicts —
inference_engine.py:615 and :655). Both are expressed here as one index-returning routine so
the suppression order stays provably the same for every caller.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

_EPS = 1e-6


def box_iou(a: Sequence[float], b: Sequence[float]) -> float:
    """Intersection-over-union of two ``[x1, y1, x2, y2]`` boxes in any shared unit."""
    inter_x1 = max(a[0], b[0])
    inter_y1 = max(a[1], b[1])
    inter_x2 = min(a[2], b[2])
    inter_y2 = min(a[3], b[3])

    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter = inter_w * inter_h

    if inter == 0.0:
        return 0.0

    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])

    return inter / (area_a + area_b - inter + _EPS)


def nms_indices(
    boxes: Sequence[Sequence[float]],
    scores: Sequence[float],
    iou_threshold: float,
) -> list[int]:
    """Greedy NMS. Returns the indices to keep, highest score first.

    Boxes must share a coordinate space; the caller decides whether that is normalised or
    absolute pixels.
    """
    if not boxes:
        return []

    box_array = np.asarray(boxes, dtype=np.float64)
    score_array = np.asarray(scores, dtype=np.float64)
    order = score_array.argsort()[::-1]

    keep: list[int] = []

    while order.size > 0:
        best = int(order[0])
        keep.append(best)

        if order.size == 1:
            break

        rest = order[1:]

        xx1 = np.maximum(box_array[best, 0], box_array[rest, 0])
        yy1 = np.maximum(box_array[best, 1], box_array[rest, 1])
        xx2 = np.minimum(box_array[best, 2], box_array[rest, 2])
        yy2 = np.minimum(box_array[best, 3], box_array[rest, 3])

        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)

        area_best = (box_array[best, 2] - box_array[best, 0]) * (
            box_array[best, 3] - box_array[best, 1]
        )
        area_rest = (box_array[rest, 2] - box_array[rest, 0]) * (
            box_array[rest, 3] - box_array[rest, 1]
        )

        iou = inter / (area_best + area_rest - inter + _EPS)
        order = rest[iou < iou_threshold]

    return keep
