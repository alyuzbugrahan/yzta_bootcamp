"""The migration must reproduce exactly what the models declare.

Without this, ``Base.metadata.create_all`` (used by the repository fixtures) and
``alembic upgrade`` (used in production) drift apart, and the suite passes against a schema that
never ships.

Runs against ``AGROVISION_TEST_DATABASE_URL`` when set, so CI exercises the real PostgreSQL
migration path rather than only the SQLite one.

Synchronous by design: ``alembic/env.py`` calls ``asyncio.run``, which cannot be nested inside a
running event loop.
"""

from __future__ import annotations

import asyncio
import os
import pathlib

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.config import get_settings
from app.infra.db.models import Base

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]

TABLES = {"users", "sessions", "inspections"}


def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return config


def target_url(tmp_path) -> str:
    return os.getenv("AGROVISION_TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path}/mig.db"


async def _wipe(url: str) -> None:
    """Drop everything, including Alembic's own bookkeeping table.

    The repository fixtures share this database and leave their own tables behind, so a
    migration run starting from a dirty schema would fail for the wrong reason.
    """
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                await connection.execute(text("DROP SCHEMA public CASCADE"))
                await connection.execute(text("CREATE SCHEMA public"))
            else:
                await connection.run_sync(Base.metadata.drop_all)
                await connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    finally:
        await engine.dispose()


async def _reflect_tables(url: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return set(
                await connection.run_sync(
                    lambda sync_conn: sync_conn.dialect.get_table_names(sync_conn)
                )
            )
    finally:
        await engine.dispose()


async def _schema_diff(url: str) -> list:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_conn: compare_metadata(
                    MigrationContext.configure(sync_conn), Base.metadata
                )
            )
    finally:
        await engine.dispose()


@pytest.fixture
def migrated(tmp_path, monkeypatch) -> str:
    """Apply every migration to a clean database and return its URL."""
    url = target_url(tmp_path)

    monkeypatch.setenv("AGROVISION_DATABASE__URL", url)
    get_settings.cache_clear()

    asyncio.run(_wipe(url))
    command.upgrade(alembic_config(), "head")

    yield url

    get_settings.cache_clear()


def test_migration_creates_every_table(migrated):
    assert TABLES <= asyncio.run(_reflect_tables(migrated))


def test_migration_matches_the_models(migrated):
    """Autogenerate must find nothing left to do."""
    diff = asyncio.run(_schema_diff(migrated))

    assert diff == [], f"schema drift between models and migration: {diff}"


def test_downgrade_removes_the_schema(migrated):
    command.downgrade(alembic_config(), "base")

    assert not (TABLES & asyncio.run(_reflect_tables(migrated)))


def test_migrations_apply_to_a_table_that_already_has_rows(tmp_path, monkeypatch):
    """Schema changes must survive real data, not just an empty test database.

    An earlier draft of the revocation column was NOT NULL with a CURRENT_TIMESTAMP default.
    That migrates cleanly against an empty table and fails against a populated one — SQLite
    rejects a non-constant default in ADD COLUMN — so the empty-database test would have
    passed it straight through to a deployment.
    """
    url = target_url(tmp_path)
    monkeypatch.setenv("AGROVISION_DATABASE__URL", url)
    get_settings.cache_clear()

    asyncio.run(_wipe(url))

    config = alembic_config()
    revisions = [script.revision for script in _walk_revisions()]

    # Stop at the first revision, populate, then continue to head.
    command.upgrade(config, revisions[0])
    asyncio.run(_insert_user(url))
    command.upgrade(config, "head")

    assert asyncio.run(_count_users(url)) == 1, "existing row did not survive the migration"

    get_settings.cache_clear()


def _walk_revisions():
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_config())
    return list(reversed(list(script.walk_revisions())))


async def _insert_user(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (email, password_hash, is_active, created_at) "
                    "VALUES ('existing@example.com', 'hash', :active, :created)"
                ),
                {"active": True, "created": "2026-01-01T00:00:00+00:00"},
            )
    finally:
        await engine.dispose()


async def _count_users(url: str) -> int:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT COUNT(*) FROM users"))
            return result.scalar_one()
    finally:
        await engine.dispose()
