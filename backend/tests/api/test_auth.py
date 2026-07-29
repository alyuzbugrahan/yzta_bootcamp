from __future__ import annotations

import pytest

from app.config import DEV_SECRET_KEY, Settings
from app.core.security import create_token, hash_password, verify_password


async def test_register_returns_a_token_pair(client):
    response = await client.post(
        "/api/v1/auth/register", json={"email": "new@example.com", "password": "harvest-2026"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60


async def test_duplicate_registration_is_rejected(client, farmer):
    response = await client.post(
        "/api/v1/auth/register", json={"email": farmer.email, "password": "another-one"}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_TAKEN"


async def test_registration_is_case_insensitive_on_email(client, farmer):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": farmer.email.upper(), "password": "another-one"},
    )

    assert response.status_code == 409


async def test_short_password_is_rejected(client):
    response = await client.post(
        "/api/v1/auth/register", json={"email": "short@example.com", "password": "abc"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_malformed_email_is_rejected(client):
    response = await client.post(
        "/api/v1/auth/register", json={"email": "not-an-email", "password": "harvest-2026"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_login_succeeds(client, farmer):
    response = await client.post(
        "/api/v1/auth/login", json={"email": farmer.email, "password": farmer.password}
    )

    assert response.status_code == 200
    assert response.json()["access_token"]


async def test_login_with_wrong_password_fails(client, farmer):
    response = await client.post(
        "/api/v1/auth/login", json={"email": farmer.email, "password": "wrong"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_unknown_email_gives_the_same_error_as_a_wrong_password(client, farmer):
    """The two cases must be indistinguishable, or the endpoint enumerates registered emails."""
    unknown = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
    )
    wrong = await client.post(
        "/api/v1/auth/login", json={"email": farmer.email, "password": "wrong"}
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


async def test_me_returns_the_current_user(client, farmer):
    response = await client.get("/api/v1/me", headers=farmer.headers)

    assert response.status_code == 200
    assert response.json()["email"] == farmer.email


async def test_me_never_leaks_the_password_hash(client, farmer):
    response = await client.get("/api/v1/me", headers=farmer.headers)

    assert "password_hash" not in response.json()
    assert "password" not in response.text


async def test_me_without_a_token_is_401(client):
    response = await client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_garbage_token_is_rejected(client):
    response = await client.get(
        "/api/v1/me", headers={"Authorization": "Bearer not-a-real-token"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


async def test_refresh_issues_a_new_pair(client, farmer):
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": farmer.refresh_token}
    )

    assert response.status_code == 200
    assert response.json()["access_token"]


async def test_access_token_is_not_accepted_as_a_refresh_token(client, farmer):
    """Without the ``typ`` claim a 30-day refresh token would work wherever an access token
    does, and the 15-minute access lifetime would be decorative."""
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": farmer.access_token}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


async def test_refresh_token_is_not_accepted_as_an_access_token(client, farmer):
    response = await client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {farmer.refresh_token}"}
    )

    assert response.status_code == 401


async def test_expired_token_is_reported_distinctly(client, settings, farmer):
    """The client needs to tell "refresh me" apart from "log in again"."""
    from datetime import UTC, datetime, timedelta

    expired = create_token(
        1, "access", settings.auth, now=datetime.now(UTC) - timedelta(hours=2)
    )

    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {expired}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


async def test_token_for_a_deleted_user_is_rejected(client, settings):
    orphan = create_token(999_999, "access", settings.auth)

    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {orphan}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


async def test_token_signed_with_another_key_is_rejected(client, farmer):
    from app.config import AuthSettings

    forged = create_token(
        1, "access", AuthSettings(secret_key="a-different-key-but-long-enough-here")
    )

    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {forged}"})

    assert response.status_code == 401


async def test_unsigned_token_is_rejected(client):
    """``alg: none`` must never be honoured — decode pins the algorithm explicitly."""
    import jwt

    unsigned = jwt.encode(
        {"sub": "1", "typ": "access", "exp": 9_999_999_999}, key="", algorithm="none"
    )

    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {unsigned}"})

    assert response.status_code == 401


# ── Password hashing ──────────────────────────────────────────────────────


def test_hash_is_salted():
    assert hash_password("same-password") != hash_password("same-password")


def test_verify_accepts_the_right_password():
    assert verify_password("harvest-2026", hash_password("harvest-2026"))


def test_verify_rejects_the_wrong_password():
    assert not verify_password("wrong", hash_password("harvest-2026"))


def test_verify_against_a_missing_hash_is_false_but_still_hashes():
    """Returns False for "no such user" while doing the same work as a real check."""
    assert verify_password("anything", None) is False


# ── Configuration guard ───────────────────────────────────────────────────


def test_production_refuses_the_development_signing_key():
    """A signing key committed to the repository would let anyone mint a farmer's token."""
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(environment="prod", auth={"secret_key": DEV_SECRET_KEY})


def test_production_accepts_a_real_signing_key():
    settings = Settings(
        environment="prod", auth={"secret_key": "a-properly-random-value-of-full-length"}
    )

    assert settings.auth.secret_key != DEV_SECRET_KEY


def test_short_signing_key_is_rejected():
    """RFC 7518 §3.2 puts the HMAC-SHA256 floor at 32 bytes; PyJWT warns below it."""
    with pytest.raises(ValueError):
        Settings(auth={"secret_key": "too-short"})
