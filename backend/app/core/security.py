"""Password hashing and JWT issuance.

New in the web version — a desktop app on the farmer's own machine had nothing to authenticate.
"""

from __future__ import annotations

import uuid as uuid_module
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import AuthSettings

TokenType = Literal["access", "refresh", "ws"]

# WebSocket tickets are short-lived because they travel in a query string, which is logged by
# proxies and kept in browser history. A browser cannot set an Authorization header on a
# WebSocket handshake, so an access token in the URL would leak a 15-minute credential into
# those logs; a ticket that expires in a minute and only names one session does not.
WS_TICKET_TTL_SECONDS = 60

# Argon2id at library defaults: memory-hard, and the current password-hashing recommendation.
_hasher = PasswordHasher()

# Verified against when no user matches, so a login attempt costs the same whether or not the
# address exists. Without this, response timing discloses which emails are registered.
_DUMMY_HASH = _hasher.hash("timing-equalisation-placeholder")


class TokenError(Exception):
    """A token was missing, malformed, expired, or of the wrong type."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: int
    token_type: TokenType
    jti: str
    expires_at: datetime
    issued_at: datetime | None = None
    generation: int = 0
    session_uuid: str | None = None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Check a password.

    Passing ``None`` still performs a hash verification against a dummy value, so the caller can
    keep the "no such user" branch indistinguishable in timing from a wrong password.
    """
    try:
        _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash predates the current Argon2 parameters."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def create_token(
    user_id: int,
    token_type: TokenType,
    settings: AuthSettings,
    now: datetime | None = None,
    session_uuid: str | None = None,
    generation: int = 0,
) -> str:
    now = now or datetime.now(UTC)

    if token_type == "access":
        expires = now + timedelta(minutes=settings.access_ttl_minutes)
    elif token_type == "ws":
        expires = now + timedelta(seconds=WS_TICKET_TTL_SECONDS)
    else:
        expires = now + timedelta(days=settings.refresh_ttl_days)

    payload = {
        "sub": str(user_id),
        # Distinguishes the token classes. Without it a 30-day refresh token would be accepted
        # as a 15-minute access token, silently defeating the short access lifetime.
        "typ": token_type,
        "jti": uuid_module.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        # Absent on tokens minted before revocation existed; those read back as generation 0,
        # which matches the default on every existing row.
        "gen": generation,
    }

    if session_uuid is not None:
        # Binds a ticket to one session, so it cannot be replayed against another.
        payload["sid"] = session_uuid

    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(
    token: str, expected_type: TokenType, settings: AuthSettings
) -> TokenClaims:
    """Decode and validate a token, or raise :class:`TokenError`."""
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            # Pinned explicitly: accepting the token's own `alg` is what enables
            # algorithm-confusion attacks, including "none".
            algorithms=[settings.algorithm],
            options={"require": ["exp", "sub", "typ", "iat"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("TOKEN_EXPIRED", "Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("TOKEN_INVALID", "Token is not valid") from exc

    if payload.get("typ") != expected_type:
        raise TokenError(
            "TOKEN_INVALID", f"Expected a {expected_type} token, got {payload.get('typ')!r}"
        )

    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenError("TOKEN_INVALID", "Token subject is not a user id") from exc

    return TokenClaims(
        user_id=user_id,
        token_type=expected_type,
        jti=payload.get("jti", ""),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
        generation=int(payload.get("gen", 0) or 0),
        session_uuid=payload.get("sid"),
    )


def token_generation(user) -> int:
    return int(getattr(user, "token_generation", 0) or 0)


def is_revoked(claims: TokenClaims, user) -> bool:
    """True when the token predates the user's current token generation.

    Exact: both sides are integers, so there is no window in which a revocation has been
    recorded but not yet taken effect, and no risk of rejecting a token that was just issued.
    """
    return claims.generation < token_generation(user)
