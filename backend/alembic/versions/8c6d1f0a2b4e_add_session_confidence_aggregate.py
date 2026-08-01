"""add session confidence aggregate

Revision ID: 8c6d1f0a2b4e
Revises: 4f2c8a7d91be
Create Date: 2026-08-01 12:10:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8c6d1f0a2b4e"
down_revision: str | None = "4f2c8a7d91be"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "avg_confidence",
                sa.Float(),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("manual_total_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("manual_defect_count", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "ck_sessions_manual_total_nonneg",
            "manual_total_count IS NULL OR manual_total_count >= 0",
        )
        batch_op.create_check_constraint(
            "ck_sessions_manual_defect_nonneg",
            "manual_defect_count IS NULL OR manual_defect_count >= 0",
        )
        batch_op.create_check_constraint(
            "ck_sessions_manual_counts_valid",
            "(manual_total_count IS NULL AND manual_defect_count IS NULL) OR "
            "(manual_total_count IS NOT NULL AND manual_defect_count IS NOT NULL "
            "AND manual_defect_count <= manual_total_count)",
        )

    # Existing rows predate the incremental aggregate. A correlated subquery works on both
    # SQLite and PostgreSQL, preserving the project's dialect-neutral migration path.
    op.execute(
        """
        UPDATE sessions
        SET avg_confidence = COALESCE(
            (SELECT AVG(inspections.confidence)
             FROM inspections
             WHERE inspections.session_id = sessions.id),
            0
        )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_constraint("ck_sessions_manual_counts_valid", type_="check")
        batch_op.drop_constraint("ck_sessions_manual_defect_nonneg", type_="check")
        batch_op.drop_constraint("ck_sessions_manual_total_nonneg", type_="check")
        batch_op.drop_column("manual_defect_count")
        batch_op.drop_column("manual_total_count")
        batch_op.drop_column("avg_confidence")
