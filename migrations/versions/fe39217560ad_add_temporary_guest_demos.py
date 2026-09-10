"""Add temporary guest demos and shared guest usage budgets.

Revision ID: fe39217560ad
Revises: 7d38ed240f12
"""

import sqlalchemy as sa
from alembic import op

revision = "fe39217560ad"
down_revision = "7d38ed240f12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspaces", sa.Column("demo_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "workspaces",
        sa.Column("demo_agent_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_workspaces_demo_expires_at", "workspaces", ["demo_expires_at"])
    op.create_table(
        "demo_daily_usage",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("sessions", sa.Integer(), server_default="0", nullable=False),
        sa.Column("bedrock_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("sessions >= 0", name="nonnegative_demo_sessions"),
        sa.CheckConstraint("bedrock_attempts >= 0", name="nonnegative_demo_bedrock_attempts"),
        sa.PrimaryKeyConstraint("day"),
    )


def downgrade() -> None:
    op.drop_table("demo_daily_usage")
    op.drop_index("ix_workspaces_demo_expires_at", table_name="workspaces")
    op.drop_column("workspaces", "demo_agent_attempts")
    op.drop_column("workspaces", "demo_expires_at")
