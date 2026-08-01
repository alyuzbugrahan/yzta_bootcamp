"""Database fixtures.

Runs against ``AGROVISION_TEST_DATABASE_URL`` when set, otherwise a throwaway SQLite file.

PostgreSQL is the deployment target, and these tests are written to be dialect-agnostic so the
same suite can be pointed at it in CI::

    AGROVISION_TEST_DATABASE_URL=postgresql+asyncpg://agrovision:agrovision@localhost/agrovision_test pytest

The two features the port depends on — ``COUNT(*) FILTER`` and ``INSERT ... RETURNING`` — are
supported by both PostgreSQL and SQLite 3.35+, which is what makes that possible.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.models import Base, User
from app.infra.db.session import create_engine, create_session_factory
from app.infra.repositories.inspection_repository import InspectionRepository
from app.infra.repositories.session_repository import SessionRepository
from app.infra.repositories.user_repository import UserRepository
from app.services.session_service import SessionService


def database_url(tmp_path) -> str:
    configured = os.getenv("AGROVISION_TEST_DATABASE_URL")
    if configured:
        return configured
    return f"sqlite+aiosqlite:///{tmp_path}/test.db"


@pytest_asyncio.fixture
async def engine(tmp_path) -> AsyncIterator:
    engine = create_engine(database_url(tmp_path))

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def db(engine) -> AsyncIterator[AsyncSession]:
    factory = create_session_factory(engine)
    async with factory() as session:
        yield session


@pytest.fixture
def users(db) -> UserRepository:
    return UserRepository(db)


@pytest.fixture
def sessions(db) -> SessionRepository:
    return SessionRepository(db)


@pytest.fixture
def inspections(db) -> InspectionRepository:
    return InspectionRepository(db)


@pytest.fixture
def service(sessions, inspections) -> SessionService:
    return SessionService(sessions, inspections)


@pytest_asyncio.fixture
async def farmer(users, db) -> User:
    user = await users.create("Farmer@Example.COM", "hash-a")
    await db.commit()
    return user


@pytest_asyncio.fixture
async def other_farmer(users, db) -> User:
    user = await users.create("neighbour@example.com", "hash-b")
    await db.commit()
    return user
