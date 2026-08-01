"""add per-session fig weight

Revision ID: b7e4c1a923f0
Revises: 8c6d1f0a2b4e
Create Date: 2026-08-01 19:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e4c1a923f0"
down_revision: str | None = "8c6d1f0a2b4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fig_weight_g", sa.Float(), nullable=True))
        batch_op.create_check_constraint(
            "ck_sessions_fig_weight_valid",
            "fig_weight_g IS NULL OR (fig_weight_g > 0 AND fig_weight_g <= 1000)",
        )


def downgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_constraint("ck_sessions_fig_weight_valid", type_="check")
        batch_op.drop_column("fig_weight_g")
