"""Phase 6: rate limits, transport guards and token revocation, over HTTP."""

from __future__ import annotations

import pytest

from app.config import DEV_SECRET_KEY, Settings

# ── Auth rate limiting ────────────────────────────────────────────────────


async def test_login_attempts_are_rate_limited(client, farmer):
    """Blunts online password guessing. Keyed by IP, because there is no trusted user yet."""
    statuses = []
    for _ in range(15):
        response = await client.post(
            "/api/v1/auth/login", json={"email": farmer.email, "password": "wrong"}
        )
        statuses.append(response.status_code)

    assert 429 in statuses, "credential endpoint accepted unlimited attempts"


async def test_rate_limited_response_carries_a_retry_after(client, farmer):
    for _ in range(15):
        response = await client.post(
            "/api/v1/auth/login", json={"email": farmer.email, "password": "wrong"}
        )
        if response.status_code == 429:
            break

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert "retry-after" in {k.lower() for k in response.headers}


async def test_registration_is_rate_limited(client):
    statuses = []
    for index in range(15):
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": f"user{index}@example.com", "password": "harvest-2026"},
        )
        statuses.append(response.status_code)

    assert 429 in statuses


async def test_authenticated_endpoints_are_not_throttled_by_the_auth_limiter(client, farmer):
    """The credential limiter must not bleed into ordinary use — a farmer scanning a busy belt
    makes far more API calls than a login flow ever does."""
    statuses = [
        (await client.get("/api/v1/sessions", headers=farmer.headers)).status_code
        for _ in range(30)
    ]

    assert set(statuses) == {200}


# ── Transport guards ──────────────────────────────────────────────────────


async def test_security_headers_are_present(client):
    response = await client.get("/api/v1/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


async def test_hsts_is_absent_when_not_configured(client):
    """Asserting HSTS from a plain-HTTP dev server would pin developers to https://localhost."""
    response = await client.get("/api/v1/health")

    assert "strict-transport-security" not in {k.lower() for k in response.headers}


async def test_security_headers_are_on_error_responses_too(client):
    response = await client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_oversized_body_is_rejected(client):
    """Starlette imposes no body limit, so without the middleware this streams into memory."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "a@example.com", "password": "x" * (300 * 1024)},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ── Token revocation ──────────────────────────────────────────────────────


async def test_logout_all_invalidates_existing_access_tokens(client, farmer):
    """The remedy for a stolen token, which otherwise stays valid for its full lifetime."""
    assert (await client.get("/api/v1/me", headers=farmer.headers)).status_code == 200

    revoked = await client.post("/api/v1/auth/logout-all", headers=farmer.headers)
    assert revoked.status_code == 204

    after = await client.get("/api/v1/me", headers=farmer.headers)

    assert after.status_code == 401
    assert after.json()["error"]["code"] == "TOKEN_INVALID"


async def test_logout_all_invalidates_refresh_tokens(client, farmer):
    """A refresh token lives thirty days; revocation is meaningless if it survives."""
    await client.post("/api/v1/auth/logout-all", headers=farmer.headers)

    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": farmer.refresh_token}
    )

    assert response.status_code == 401


async def test_logging_in_again_after_revocation_works(client, farmer):
    await client.post("/api/v1/auth/logout-all", headers=farmer.headers)

    fresh = await client.post(
        "/api/v1/auth/login", json={"email": farmer.email, "password": farmer.password}
    )
    assert fresh.status_code == 200

    headers = {"Authorization": f"Bearer {fresh.json()['access_token']}"}

    assert (await client.get("/api/v1/me", headers=headers)).status_code == 200


async def test_revocation_does_not_affect_other_users(client, farmer, neighbour):
    await client.post("/api/v1/auth/logout-all", headers=farmer.headers)

    assert (await client.get("/api/v1/me", headers=neighbour.headers)).status_code == 200


async def test_logout_all_requires_authentication(client):
    assert (await client.post("/api/v1/auth/logout-all")).status_code == 401


# ── Configuration guards ──────────────────────────────────────────────────


def test_wildcard_cors_is_refused_outside_dev():
    """With credentials enabled a wildcard origin would let any site act as the logged-in
    farmer. Caught at boot rather than as a confusing browser error."""
    with pytest.raises(ValueError, match="CORS"):
        Settings(
            environment="prod",
            cors_origins=["*"],
            auth={"secret_key": "a-properly-random-value-of-full-length"},
        )


def test_explicit_cors_origins_are_accepted():
    settings = Settings(
        environment="prod",
        cors_origins=["https://figion.example"],
        auth={"secret_key": "a-properly-random-value-of-full-length"},
    )

    assert settings.cors_origins == ["https://figion.example"]


def test_wildcard_is_allowed_in_dev():
    assert Settings(environment="dev", cors_origins=["*"]).cors_origins == ["*"]


def test_dev_secret_still_refused_in_prod():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(environment="prod", auth={"secret_key": DEV_SECRET_KEY})


# ── Write durability ──────────────────────────────────────────────────────


async def test_token_works_on_the_very_next_request(client):
    """A client acting on a response must never outrun the write that produced it.

    Since FastAPI 0.106 the exit half of a ``yield`` dependency runs *after* the response is
    sent, so committing there handed the caller a token before the user row was durable. The
    next request then failed with "token subject no longer exists". Found by the load harness,
    not by the suite — every existing test happened to tolerate the lag.
    """
    registration = await client.post(
        "/api/v1/auth/register",
        json={"email": "durable@example.com", "password": "harvest-2026"},
    )
    assert registration.status_code == 201

    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}

    assert (await client.get("/api/v1/me", headers=headers)).status_code == 200


async def test_session_is_readable_immediately_after_creation(client, farmer):
    """The client opens a WebSocket against this uuid at once, on another database session."""
    created = await client.post("/api/v1/sessions", json={}, headers=farmer.headers)
    session_uuid = created.json()["uuid"]

    ticket = await client.post(
        f"/api/v1/sessions/{session_uuid}/ticket", headers=farmer.headers
    )

    assert ticket.status_code == 200


async def test_revocation_is_durable_immediately(client, farmer):
    await client.post("/api/v1/auth/logout-all", headers=farmer.headers)

    assert (await client.get("/api/v1/me", headers=farmer.headers)).status_code == 401
