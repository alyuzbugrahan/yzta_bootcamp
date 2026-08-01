"""Storage backends and key construction.

The S3 tests run against MinIO when ``AGROVISION_TEST_S3_ENDPOINT`` is set (``docker compose up -d
minio``) and skip otherwise, so the suite still runs on a machine with no object store.
"""

from __future__ import annotations

import os

import pytest

from app.infra.storage.base import (
    ObjectNotFound,
    StorageError,
    image_key,
    session_prefix,
    validate_key,
)
from app.infra.storage.local import LocalStorage

JPEG = b"\xff\xd8\xff\xe0fake-jpeg-body\xff\xd9"


# ── Key construction ──────────────────────────────────────────────────────


def test_image_key_shape():
    """Keeps the desktop filename shape, with the date folder swapped for the owner."""
    assert image_key(7, "BATCH_20260729_101500", 42, "Aflatoxin") == (
        "u7/BATCH_20260729_101500/fig_0042_Aflatoxin.jpg"
    )


def test_image_key_zero_pads_like_the_desktop():
    assert image_key(1, "B", 3, "Healthy").endswith("fig_0003_Healthy.jpg")


def test_session_prefix_covers_only_that_session():
    assert session_prefix(7, "BATCH_A") == "u7/BATCH_A/"


@pytest.mark.parametrize(
    "key",
    [
        "u1/../../etc/passwd",
        "/absolute/path.jpg",
        "u1\\windows\\path.jpg",
        "u1/BATCH/../../../secret.jpg",
        "u1/./fig.jpg",
        "u1//fig.jpg",
        "..",
        "",
        " u1/leading-space.jpg",
    ],
)
def test_unsafe_keys_are_rejected(key):
    """Keys are server-built today, but a traversal guard is cheap and failing loudly is
    better than writing outside the storage root.

    ``..`` needs its own check: "." is a legal filename character, so a bare character-class
    test accepts ``..`` as an ordinary segment. LocalStorage has a second ``is_relative_to``
    guard, but S3Storage relies on this function alone.
    """
    with pytest.raises(StorageError):
        validate_key(key)


def test_ordinary_key_passes():
    assert validate_key("u1/BATCH_A/fig_0001_Healthy.jpg")


# ── LocalStorage ──────────────────────────────────────────────────────────


@pytest.fixture
def local(tmp_path) -> LocalStorage:
    return LocalStorage(tmp_path / "images")


async def test_local_round_trip(local):
    await local.put("u1/BATCH_A/fig_0001_Healthy.jpg", JPEG)

    assert await local.get("u1/BATCH_A/fig_0001_Healthy.jpg") == JPEG


async def test_local_creates_nested_directories(local):
    await local.put("u9/BATCH_Z/fig_0001_Healthy.jpg", JPEG)

    assert (local.root / "u9" / "BATCH_Z" / "fig_0001_Healthy.jpg").is_file()


async def test_local_missing_object_raises(local):
    with pytest.raises(ObjectNotFound):
        await local.get("u1/BATCH_A/nope.jpg")


async def test_local_exists(local):
    assert await local.exists("u1/BATCH_A/fig_0001_Healthy.jpg") is False
    await local.put("u1/BATCH_A/fig_0001_Healthy.jpg", JPEG)
    assert await local.exists("u1/BATCH_A/fig_0001_Healthy.jpg") is True


async def test_local_leaves_no_partial_files(local):
    """Written to a .part file and renamed, so a crash cannot leave a truncated JPEG that
    later reads as a corrupt image."""
    await local.put("u1/BATCH_A/fig_0001_Healthy.jpg", JPEG)

    assert not list(local.root.rglob("*.part"))


async def test_local_delete_prefix_removes_the_session(local):
    for seq in range(3):
        await local.put(f"u1/BATCH_A/fig_{seq:04d}_Healthy.jpg", JPEG)
    await local.put("u1/BATCH_B/fig_0001_Healthy.jpg", JPEG)

    removed = await local.delete_prefix(session_prefix(1, "BATCH_A"))

    assert removed == 3
    assert await local.exists("u1/BATCH_B/fig_0001_Healthy.jpg") is True


async def test_local_delete_prefix_of_nothing_is_zero(local):
    assert await local.delete_prefix(session_prefix(1, "BATCH_MISSING")) == 0


async def test_local_delete_does_not_touch_another_users_images(local):
    await local.put("u1/BATCH_A/fig_0001_Healthy.jpg", JPEG)
    await local.put("u2/BATCH_A/fig_0001_Healthy.jpg", JPEG)

    await local.delete_prefix(session_prefix(1, "BATCH_A"))

    assert await local.exists("u2/BATCH_A/fig_0001_Healthy.jpg") is True


async def test_local_rejects_traversal_on_write(local):
    with pytest.raises(StorageError):
        await local.put("../escaped.jpg", JPEG)


def test_local_has_no_presigned_url(local):
    """No direct URL, so the API streams the bytes and keeps the ownership check on the
    request path."""
    assert local.presigned_url("u1/BATCH_A/fig_0001_Healthy.jpg") is None


# ── S3Storage (MinIO) ─────────────────────────────────────────────────────

S3_ENDPOINT = os.getenv("AGROVISION_TEST_S3_ENDPOINT")

s3_only = pytest.mark.skipif(
    not S3_ENDPOINT, reason="set AGROVISION_TEST_S3_ENDPOINT to test against MinIO"
)


@pytest.fixture
def s3():
    from app.infra.storage.s3 import S3Storage

    storage = S3Storage(
        bucket="agrovision-test",
        endpoint_url=S3_ENDPOINT,
        access_key=os.getenv("AGROVISION_TEST_S3_KEY", "agrovision"),
        secret_key=os.getenv("AGROVISION_TEST_S3_SECRET", "agrovision-secret"),
    )
    storage.ensure_bucket()
    return storage


@s3_only
async def test_s3_round_trip(s3):
    await s3.put("u1/BATCH_S3/fig_0001_Healthy.jpg", JPEG)

    assert await s3.get("u1/BATCH_S3/fig_0001_Healthy.jpg") == JPEG


@s3_only
async def test_s3_missing_object_raises(s3):
    with pytest.raises(ObjectNotFound):
        await s3.get("u1/BATCH_S3/absent.jpg")


@s3_only
async def test_s3_exists(s3):
    await s3.put("u1/BATCH_EXISTS/fig_0001_Healthy.jpg", JPEG)

    assert await s3.exists("u1/BATCH_EXISTS/fig_0001_Healthy.jpg") is True
    assert await s3.exists("u1/BATCH_EXISTS/absent.jpg") is False


@s3_only
async def test_s3_delete_prefix(s3):
    for seq in range(5):
        await s3.put(f"u1/BATCH_DEL/fig_{seq:04d}_Healthy.jpg", JPEG)
    await s3.put("u1/BATCH_KEEP/fig_0001_Healthy.jpg", JPEG)

    removed = await s3.delete_prefix(session_prefix(1, "BATCH_DEL"))

    assert removed == 5
    assert await s3.exists("u1/BATCH_KEEP/fig_0001_Healthy.jpg") is True


@s3_only
async def test_s3_presigned_url_is_fetchable(s3):
    """The URL must actually serve the object — a malformed signature fails here, not in
    production."""
    import httpx

    key = "u1/BATCH_PRESIGN/fig_0001_Healthy.jpg"
    await s3.put(key, JPEG)

    url = s3.presigned_url(key, expires_in=60)
    async with httpx.AsyncClient() as client:
        response = await client.get(url)

    assert response.status_code == 200
    assert response.content == JPEG


@s3_only
async def test_s3_presigned_url_expires(s3):
    """Short TTLs are the only thing limiting a leaked URL, so expiry must be enforced."""
    import asyncio

    import httpx

    key = "u1/BATCH_EXPIRY/fig_0001_Healthy.jpg"
    await s3.put(key, JPEG)

    url = s3.presigned_url(key, expires_in=1)
    await asyncio.sleep(2)

    async with httpx.AsyncClient() as client:
        response = await client.get(url)

    assert response.status_code == 403
