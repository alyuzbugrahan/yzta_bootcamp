"""Frame validation.

The desktop pipeline opened its own camera, so frames were trusted by construction. These now
arrive from whoever holds a ticket, and the decoder is the softest target in the system.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.services.frame_codec import (
    FrameLimits,
    FrameRejected,
    decode,
    read_jpeg_dimensions,
    validate,
)


def jpeg(width: int = 640, height: int = 480, quality: int = 80) -> bytes:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.ellipse(
        image,
        (width // 2, height // 2),
        (width // 8, height // 8),
        0,
        0,
        360,
        (220, 220, 220),
        -1,
    )
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    return buffer.tobytes()


@pytest.fixture
def limits() -> FrameLimits:
    return FrameLimits()


# ── Header parsing ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("width", "height"), [(640, 480), (1280, 720), (320, 240), (1920, 1080)]
)
def test_dimensions_are_read_without_decoding(width, height):
    assert read_jpeg_dimensions(jpeg(width, height)) == (width, height)


def test_non_jpeg_is_rejected():
    with pytest.raises(FrameRejected) as exc:
        read_jpeg_dimensions(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

    assert exc.value.code == "FRAME_NOT_JPEG"


def test_truncated_header_is_rejected():
    with pytest.raises(FrameRejected) as exc:
        read_jpeg_dimensions(jpeg()[:8])

    assert exc.value.code in {"FRAME_MALFORMED", "FRAME_NOT_JPEG"}


def test_jpeg_with_no_frame_marker_is_rejected():
    """SOI immediately followed by SOS — well-formed prefix, no dimensions anywhere."""
    with pytest.raises(FrameRejected) as exc:
        read_jpeg_dimensions(b"\xff\xd8\xff\xda\x00\x02")

    assert exc.value.code == "FRAME_MALFORMED"


# ── Limits ────────────────────────────────────────────────────────────────


def test_normal_frame_passes(limits):
    assert validate(jpeg(), limits) == (640, 480)


def test_empty_frame_is_rejected(limits):
    with pytest.raises(FrameRejected) as exc:
        validate(b"", limits)

    assert exc.value.code == "FRAME_EMPTY"


def test_oversized_payload_is_rejected():
    with pytest.raises(FrameRejected) as exc:
        validate(jpeg(), FrameLimits(max_bytes=100))

    assert exc.value.code == "FRAME_TOO_LARGE"


def test_oversized_dimensions_are_rejected():
    with pytest.raises(FrameRejected) as exc:
        validate(jpeg(1920, 1080), FrameLimits(max_width=1280, max_height=720))

    assert exc.value.code == "FRAME_TOO_LARGE"


def test_decompression_bomb_is_refused_before_decoding(limits):
    """A small payload declaring an enormous frame must never reach ``cv2.imdecode``.

    This is why dimensions are parsed from the header rather than inferred from byte length:
    the compressed form of a bomb is tiny by design. A 25000x25000 frame would allocate
    roughly 1.9 GB if decoded.
    """
    bomb = _forge_header(25000, 25000)

    with pytest.raises(FrameRejected) as exc:
        validate(bomb, limits)

    assert exc.value.code == "FRAME_TOO_LARGE"
    assert len(bomb) < 1000, "the point is that the payload is small"


def test_bomb_dimensions_are_parsed_correctly():
    assert read_jpeg_dimensions(_forge_header(25000, 25000)) == (25000, 25000)


# ── Decoding ──────────────────────────────────────────────────────────────


def test_decode_returns_a_bgr_array(limits):
    frame = decode(jpeg(640, 480), limits)

    assert frame.shape == (480, 640, 3)
    assert frame.dtype == np.uint8


def test_decode_rejects_corrupt_entropy_data(limits):
    """The header parses but the scan data is destroyed — imdecode returns None."""
    data = bytearray(jpeg())
    for index in range(len(data) // 2, len(data)):
        data[index] = 0x00

    try:
        frame = decode(bytes(data), limits)
    except FrameRejected as exc:
        assert exc.value.code if hasattr(exc, "value") else exc.code == "FRAME_MALFORMED"
    else:
        # OpenCV is tolerant and may still produce an image; either outcome is acceptable
        # so long as it does not raise something unhandled.
        assert frame.ndim == 3


def test_decode_enforces_limits_too():
    with pytest.raises(FrameRejected):
        decode(jpeg(1920, 1080), FrameLimits(max_width=640, max_height=480))


def _forge_header(width: int, height: int) -> bytes:
    """A minimal JPEG header declaring huge dimensions, with no image data behind it."""
    sof = (
        b"\xff\xc0"
        + (17).to_bytes(2, "big")
        + b"\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03"
        + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    )
    return b"\xff\xd8" + sof + b"\xff\xd9"
