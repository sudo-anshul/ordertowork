"""Add global Bedrock daily budget.

Revision ID: 7d38ed240f12
Revises: 3edc2a397b7c
"""

import sqlalchemy as sa
from alembic import op

revision = "7d38ed240f12"
down_revision = "3edc2a397b7c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_daily_usage",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("attempts >= 0", name="nonnegative_agent_attempts"),
        sa.PrimaryKeyConstraint("day"),
    )


def downgrade() -> None:
    op.drop_table("agent_daily_usage")
