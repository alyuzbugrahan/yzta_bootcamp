"""SQLAlchemy models.

Ported from the DDL in ``data/database_handler.py``. The table shapes and their CHECK
constraints carry over; what changes is ownership and portability.

* ``sessions`` and ``inspections`` gain a user, because a shared server has many farmers where
  the desktop app had one operator.
* ``sessions.batch_id`` was globally ``UNIQUE`` and derived from a second-resolution timestamp
  (``data/session_manager.py:24``). Two farmers starting a scan in the same second would
  collide, so uniqueness is now scoped per user.
* ``inspections.image_path`` becomes ``image_key``: an object-store key, not a filesystem path
  a browser cannot resolve.
* No PostgreSQL-only types are used. Email uniqueness is enforced on a normalised lowercase
  column rather than ``CITEXT``, which avoids depending on an extension being installed.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain.models import DECISION_AFLATOXIN, DECISION_HEALTHY


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stored lowercased by UserRepository so the unique index is effectively case-insensitive.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Bumped by "log out everywhere"; tokens carrying an older generation are refused.
    #
    # A counter rather than a timestamp. The obvious design — store the revocation instant and
    # reject tokens issued before it — cannot work: JWT ``iat`` is whole seconds, so a token
    # minted in the same second as the revocation is indistinguishable from one minted just
    # before it, and stamping fresh tokens forward to compensate makes PyJWT reject them
    # outright as not-yet-valid. Integers have no resolution to lose.
    #
    # A jti denylist would be per-process and unsound with more than one replica; this is a
    # single column every replica reads.
    token_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )

    sessions: Mapped[list[ScanSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ScanSession(Base):
    """One "start scanning → stop scanning" cycle.

    Named ``ScanSession`` rather than ``Session`` to avoid colliding with
    ``sqlalchemy.orm.Session`` at every call site. The table keeps its original name.
    """

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("total_count >= 0", name="ck_sessions_total_nonneg"),
        CheckConstraint("defect_count >= 0", name="ck_sessions_defect_nonneg"),
        CheckConstraint("defect_count <= total_count", name="ck_sessions_defect_lte_total"),
        UniqueConstraint("user_id", "batch_id", name="uq_sessions_user_batch"),
        Index("idx_sessions_user", "user_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Opaque public identifier. Sequential integer ids in URLs invite enumeration.
    uuid: Mapped[uuid_module.UUID] = mapped_column(
        Uuid, nullable=False, unique=True, index=True, default=uuid_module.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[str] = mapped_column(String(64), nullable=False)
    device_label: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Snapshotted at session start so a later slider change cannot retroactively
    # reinterpret records that were already written.
    conf_threshold: Mapped[float] = mapped_column(Float, nullable=False)

    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # NULL means the session is still open.
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    defect_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    user: Mapped[User] = relationship(back_populates="sessions")
    inspections: Mapped[list[Inspection]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    @property
    def is_open(self) -> bool:
        return self.end_time is None


class Inspection(Base):
    """One fig, recorded once."""

    __tablename__ = "inspections"
    __table_args__ = (
        CheckConstraint(
            f"decision IN ('{DECISION_HEALTHY}', '{DECISION_AFLATOXIN}')",
            name="ck_inspections_decision",
        ),
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0", name="ck_inspections_confidence"
        ),
        # Also the concurrency guard for fig_seq allocation — see InspectionRepository.record.
        UniqueConstraint("session_id", "fig_seq", name="uq_inspections_session_seq"),
        Index("idx_insp_session", "session_id"),
        Index("idx_insp_decision", "session_id", "decision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    fig_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    image_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    session: Mapped[ScanSession] = relationship(back_populates="inspections")
