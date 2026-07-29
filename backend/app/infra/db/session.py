"""Async engine and session factory.

Replaces ``DatabaseConnectionFactory`` (data/database_handler.py:75), which handed out one
shared ``sqlite3`` connection with ``check_same_thread=False``. That is correct for a single
desktop process and a write-contention bug the moment concurrent requests exist. Each request
now gets its own session from a pool.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.infra.db.models import Base


def create_engine(url: str, echo: bool = False) -> AsyncEngine:
    """Build the engine.

    SQLite needs per-connection pragmas that PostgreSQL either has by default or does not
    have at all, so they are applied only for that dialect.
    """
    if url.startswith("sqlite"):
        engine = create_async_engine(url, echo=echo, future=True)
        _install_sqlite_pragmas(engine)
        return engine

    return create_async_engine(
        url,
        echo=echo,
        future=True,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


def _install_sqlite_pragmas(engine: AsyncEngine) -> None:
    """Enable foreign keys and WAL, matching the desktop pragmas.

    ``foreign_keys`` in particular is off by default in SQLite, so without this the
    ``ON DELETE CASCADE`` behaviour the tests rely on would silently not happen.
    """
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def create_all(engine: AsyncEngine) -> None:
    """Create the schema directly. For tests and local development only.

    Production schema changes go through Alembic; see ``alembic/``.
    """
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session, committing on success and rolling back on error."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
