"""Registration, login and token refresh."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.exc import IntegrityError

from app.api.v1.schemas import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserResponse,
)
from app.core.errors import ApiError, ErrorCode, Unauthenticated
from app.core.logging import get_logger
from app.core.security import (
    TokenError,
    create_token,
    decode_token,
    hash_password,
    is_revoked,
    needs_rehash,
    token_generation,
    verify_password,
)
from app.core.throttle import throttle_auth
from app.deps import AppSettings, CurrentUser, DbSession, UserRepositoryDep

log = get_logger(__name__)

router = APIRouter(tags=["auth"])


def _issue(user, settings) -> TokenPair:
    generation = token_generation(user)

    return TokenPair(
        access_token=create_token(
            user.id, "access", settings.auth, generation=generation
        ),
        refresh_token=create_token(
            user.id, "refresh", settings.auth, generation=generation
        ),
        expires_in=settings.auth.access_ttl_minutes * 60,
    )


@router.post(
    "/auth/register",
    response_model=TokenPair,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(throttle_auth)],
)
async def register(
    payload: RegisterRequest,
    users: UserRepositoryDep,
    db: DbSession,
    settings: AppSettings,
) -> TokenPair:
    if len(payload.password) < settings.auth.min_password_length:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            f"Password must be at least {settings.auth.min_password_length} characters",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    try:
        user = await users.create(payload.email, hash_password(payload.password))
        await db.flush()
    except IntegrityError as exc:
        # The unique index is the authority, not a prior existence check — two simultaneous
        # registrations would both pass such a check.
        await db.rollback()
        raise ApiError(
            ErrorCode.EMAIL_TAKEN,
            "That email address is already registered",
            status.HTTP_409_CONFLICT,
        ) from exc

    # Committed before the token is handed out: the caller will use it immediately.
    await db.commit()

    log.info("user_registered", user_id=user.id)
    return _issue(user, settings)


@router.post(
    "/auth/login",
    response_model=TokenPair,
    dependencies=[Depends(throttle_auth)],
)
async def login(
    payload: LoginRequest,
    users: UserRepositoryDep,
    db: DbSession,
    settings: AppSettings,
) -> TokenPair:
    user = await users.get_by_email(payload.email)

    # verify_password is called even when no user matched, so the response time does not
    # disclose whether the address is registered.
    if not verify_password(payload.password, user.password_hash if user else None):
        raise ApiError(
            ErrorCode.INVALID_CREDENTIALS,
            "Email or password is incorrect",
            status.HTTP_401_UNAUTHORIZED,
        )

    if not user.is_active:
        raise ApiError(
            ErrorCode.ACCOUNT_DISABLED, "Account is disabled", status.HTTP_403_FORBIDDEN
        )

    # Transparently upgrade hashes written under older Argon2 parameters.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
        await db.commit()

    log.info("user_logged_in", user_id=user.id)
    return _issue(user, settings)


@router.post(
    "/auth/refresh",
    response_model=TokenPair,
    dependencies=[Depends(throttle_auth)],
)
async def refresh(
    payload: RefreshRequest,
    users: UserRepositoryDep,
    settings: AppSettings,
) -> TokenPair:
    """Exchange a refresh token for a new pair.

    Only a token minted with ``typ: refresh`` is accepted — an access token presented here is
    rejected, which is what keeps the short access lifetime meaningful.

    Rotation is still not per-token revocation — the old refresh token remains usable until it
    expires. What *is* enforced is the account-wide cutoff written by ``/auth/logout-all``,
    which invalidates every token issued before it. A per-token denylist would need shared
    state across replicas; see ``app/core/rate_limit.py`` for the same constraint.
    """
    try:
        claims = decode_token(payload.refresh_token, "refresh", settings.auth)
    except TokenError as exc:
        raise Unauthenticated(exc.code, str(exc)) from exc

    user = await users.get_by_id(claims.user_id)

    if user is None or not user.is_active:
        raise Unauthenticated(ErrorCode.TOKEN_INVALID, "Token subject is not usable")

    if is_revoked(claims, user):
        raise Unauthenticated(ErrorCode.TOKEN_INVALID, "Token has been revoked")

    return _issue(user, settings)


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)


@router.post("/auth/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(user: CurrentUser, db: DbSession) -> None:
    """Invalidate every token issued to this account so far.

    The remedy for a stolen refresh token, which otherwise stays valid for thirty days. Moving
    the cutoff forward is a single row write that every replica observes, unlike an in-process
    denylist.
    """
    user.token_generation = token_generation(user) + 1
    await db.commit()
    log.info("tokens_revoked", user_id=user.id)
