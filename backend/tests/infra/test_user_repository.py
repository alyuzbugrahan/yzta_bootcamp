from __future__ import annotations

import pytest

from app.infra.repositories.user_repository import normalize_email


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Farmer@Example.COM", "farmer@example.com"),
        ("  spaced@example.com  ", "spaced@example.com"),
        ("already@lower.com", "already@lower.com"),
    ],
)
def test_normalization(raw, expected):
    assert normalize_email(raw) == expected


async def test_lookup_is_case_insensitive(users, farmer):
    """Normalising on write and read gives CITEXT's behaviour without the extension."""
    found = await users.get_by_email("FARMER@EXAMPLE.COM")

    assert found is not None
    assert found.id == farmer.id


async def test_email_is_stored_normalized(farmer):
    assert farmer.email == "farmer@example.com"


async def test_duplicate_email_is_rejected(users, farmer):
    """Rejected at flush time inside ``create``, before the caller ever reaches commit."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await users.create("FARMER@example.com", "another-hash")


async def test_unknown_email_returns_none(users):
    assert await users.get_by_email("nobody@example.com") is None


async def test_exists(users, farmer):
    assert await users.exists("farmer@example.com") is True
    assert await users.exists("nobody@example.com") is False


async def test_new_user_is_active(farmer):
    assert farmer.is_active is True
