"""Dependency wiring.

The desktop app constructed its whole object graph inside ``MainWindow.__init__``
(ui/main_window.py:53), which is why none of it could be exercised without a Qt event loop.
Everything is injected here instead.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.errors import ErrorCode, Unauthenticated
from app.core.security import TokenError, decode_token, is_revoked
from app.infra.db.models import User
from app.infra.repositories.inspection_repository import InspectionRepository
from app.infra.repositories.session_repository import SessionRepository
from app.infra.repositories.user_repository import UserRepository
from app.services.session_service import SessionService

# auto_error=False so a missing header raises our own error shape rather than FastAPI's.
_bearer = HTTPBearer(auto_error=False)


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request. **Writes must commit explicitly inside the handler.**

    This deliberately does not commit on teardown. Since FastAPI 0.106 the exit half of a
    ``yield`` dependency runs *after the response has been sent*, so a commit here lands after
    the client already holds the reply — and a client that acts on that reply immediately can
    outrun its own write. Registration exhibited exactly this: the caller received a token and
    the very next request failed with "token subject no longer exists", because the user row
    had not been committed yet.

    Rollback on failure stays here, where ordering does not matter.

    Streaming endpoints must not use this session at all: the body is produced after teardown.
    ``exports.py`` opens its own for that reason.
    """
    factory = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings_dep)]


def get_user_repository(db: DbSession) -> UserRepository:
    return UserRepository(db)


def get_session_repository(db: DbSession) -> SessionRepository:
    return SessionRepository(db)


def get_inspection_repository(db: DbSession) -> InspectionRepository:
    return InspectionRepository(db)


def get_session_service(
    sessions: Annotated[SessionRepository, Depends(get_session_repository)],
    inspections: Annotated[InspectionRepository, Depends(get_inspection_repository)],
) -> SessionService:
    return SessionService(sessions, inspections)


async def get_current_user(
    settings: AppSettings,
    users: Annotated[UserRepository, Depends(get_user_repository)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    """Resolve the bearer token to a user, or raise 401.

    Every ownership decision downstream keys off the returned ``user.id``. Nothing else in the
    request — path, query or body — is trusted to identify the caller.
    """
    if credentials is None or not credentials.credentials:
        raise Unauthenticated()

    try:
        claims = decode_token(credentials.credentials, "access", settings.auth)
    except TokenError as exc:
        raise Unauthenticated(exc.code, str(exc)) from exc

    user = await users.get_by_id(claims.user_id)

    if user is None:
        # The token verified, but its subject is gone — a deleted account.
        raise Unauthenticated(ErrorCode.TOKEN_INVALID, "Token subject no longer exists")

    if not user.is_active:
        raise Unauthenticated(ErrorCode.ACCOUNT_DISABLED, "Account is disabled")

    if is_revoked(claims, user):
        raise Unauthenticated(ErrorCode.TOKEN_INVALID, "Token has been revoked")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]
SessionFactory = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
SessionRepositoryDep = Annotated[SessionRepository, Depends(get_session_repository)]
