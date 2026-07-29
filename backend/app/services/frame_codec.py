"""Validation and decoding of client-supplied frames.

The desktop pipeline read frames from a camera it opened itself, so every frame was trusted by
construction. Frames now arrive over the network from whoever holds a ticket, and
``cv2.imdecode`` on unvalidated bytes is a memory-exhaustion vector: a 40 KB JPEG can declare
30000x30000 in its header and expand to several gigabytes the moment it is decoded.

So dimensions are read from the JPEG header and checked *before* any decode is attempted. Byte
length alone is not a defence — the whole point of a decompression bomb is that the compressed
form is small.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

SOI = b"\xff\xd8"

# Start-of-frame markers, which carry the image dimensions.
_SOF_MARKERS = {
    *range(0xC0, 0xC4),
    *range(0xC5, 0xC8),
    *range(0xC9, 0xCC),
    *range(0xCD, 0xD0),
}

# Markers that stand alone: no length field follows them.
_STANDALONE = {0x01, *range(0xD0, 0xDA)}

# Start of scan — entropy-coded data follows, and any SOF has already appeared.
_SOS = 0xDA


class FrameRejected(Exception):
    """A frame failed validation. Carries a stable code for the client."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FrameLimits:
    max_bytes: int = 2 * 1024 * 1024
    max_width: int = 1920
    max_height: int = 1080

    @classmethod
    def from_settings(cls, ingest) -> FrameLimits:  # app.config.IngestSettings
        return cls(
            max_bytes=ingest.max_frame_bytes,
            max_width=ingest.max_frame_width,
            max_height=ingest.max_frame_height,
        )


def read_jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Return ``(width, height)`` from the JPEG header without decoding pixel data.

    Walks the marker segments to the first SOF. Raises :class:`FrameRejected` if the data is not
    a JPEG or is truncated before any SOF.
    """
    if len(data) < 4 or not data.startswith(SOI):
        raise FrameRejected("FRAME_NOT_JPEG", "Frame is not a JPEG")

    offset = 2
    length = len(data)

    while offset < length:
        # Segments begin with 0xFF; fill bytes may pad before the marker.
        if data[offset] != 0xFF:
            offset += 1
            continue

        while offset < length and data[offset] == 0xFF:
            offset += 1

        if offset >= length:
            break

        marker = data[offset]
        offset += 1

        if marker in _STANDALONE:
            continue

        if marker == _SOS:
            break

        if offset + 2 > length:
            break

        segment_length = int.from_bytes(data[offset : offset + 2], "big")

        if marker in _SOF_MARKERS:
            # precision(1) height(2) width(2)
            if offset + 7 > length:
                break
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            return width, height

        if segment_length < 2:
            break

        offset += segment_length

    raise FrameRejected("FRAME_MALFORMED", "JPEG header carries no frame dimensions")


def validate(data: bytes, limits: FrameLimits) -> tuple[int, int]:
    """Check a frame is acceptable. Returns its declared dimensions."""
    if not data:
        raise FrameRejected("FRAME_EMPTY", "Frame is empty")

    if len(data) > limits.max_bytes:
        raise FrameRejected(
            "FRAME_TOO_LARGE",
            f"Frame is {len(data)} bytes, limit is {limits.max_bytes}",
        )

    width, height = read_jpeg_dimensions(data)

    if width <= 0 or height <= 0:
        raise FrameRejected("FRAME_MALFORMED", "Frame has non-positive dimensions")

    if width > limits.max_width or height > limits.max_height:
        raise FrameRejected(
            "FRAME_TOO_LARGE",
            f"Frame is {width}x{height}, limit is {limits.max_width}x{limits.max_height}",
        )

    return width, height


def decode(data: bytes, limits: FrameLimits) -> np.ndarray:
    """Validate then decode to a BGR array.

    Blocking and CPU-bound for large frames; call it from a worker thread alongside inference.
    """
    validate(data, limits)

    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)

    if frame is None:
        # Header parsed but the entropy-coded data is corrupt.
        raise FrameRejected("FRAME_MALFORMED", "Frame could not be decoded")

    return frame
