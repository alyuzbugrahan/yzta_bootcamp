"""User persistence.

New in the web version — the desktop app had no concept of an account.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models import User


def normalize_email(email: str) -> str:
    """Lowercase and strip. Applied on both write and lookup so the two always agree."""
    return email.strip().lower()


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, email: str, password_hash: str) -> User:
        user = User(email=normalize_email(email), password_hash=password_hash)
        self._session.add(user)
        await self._session.flush()
        return user

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(User).where(User.email == normalize_email(email))
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    async def exists(self, email: str) -> bool:
        result = await self._session.execute(
            select(User.id).where(User.email == normalize_email(email))
        )
        return result.scalar_one_or_none() is not None
