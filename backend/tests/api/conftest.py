"""API fixtures.

Uses the same database wiring as the persistence suite, so ``AGROVISION_TEST_DATABASE_URL`` points
these tests at PostgreSQL too.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.infra.db.models import Base
from app.infra.db.session import create_engine
from app.main import create_app


def database_url(tmp_path) -> str:
    return os.getenv("AGROVISION_TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path}/api.db"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        environment="dev",
        log_json=False,
        database={"url": database_url(tmp_path)},
        # Demo mode keeps startup from requiring a model file the repository does not ship.
        model={"allow_demo": True},
    )


@pytest_asyncio.fixture
async def app(settings) -> AsyncIterator:
    engine = create_engine(settings.database.url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    application = create_app(settings)

    # Enter the lifespan so app.state.session_factory and the detector exist.
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@dataclass
class Account:
    """A registered farmer plus a ready-to-use auth header."""

    email: str
    password: str
    access_token: str
    refresh_token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


async def register(client: AsyncClient, email: str, password: str = "harvest-2026") -> Account:
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return Account(
        email=email,
        password=password,
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
    )


@pytest_asyncio.fixture
async def farmer(client) -> Account:
    return await register(client, "farmer@example.com")


@pytest_asyncio.fixture
async def neighbour(client) -> Account:
    return await register(client, "neighbour@example.com")


@pytest_asyncio.fixture
async def open_session(client, farmer) -> dict:
    response = await client.post(
        "/api/v1/sessions", json={"device_label": "Barn cam"}, headers=farmer.headers
    )
    assert response.status_code == 201, response.text
    return response.json()
