"""add dashboard range index

Revision ID: 4f2c8a7d91be
Revises: 17d4feafb8f2
Create Date: 2026-08-01 11:35:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "4f2c8a7d91be"
down_revision: str | None = "17d4feafb8f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # /reports/range always filters by owner and start_time. The previous index
    # (user_id, id) only helped history pagination, not the dashboard date window.
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.create_index(
            "idx_sessions_user_start",
            ["user_id", "start_time"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_index("idx_sessions_user_start")
