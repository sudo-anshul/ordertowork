"""Add production handover choices and delivery consent

Revision ID: 2d114ca5262f
Revises: b9180ac37e62
Create Date: 2026-09-11 13:05:02.172986

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2d114ca5262f"
down_revision: Union[str, Sequence[str], None] = "b9180ac37e62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "handovers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=True),
        sa.Column("collection_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_address", sa.Text(), nullable=True),
        sa.Column("contact_phone", sa.String(length=40), nullable=True),
        sa.Column("customer_note", sa.Text(), nullable=True),
        sa.Column("delivery_fee_cents", sa.Integer(), nullable=True),
        sa.Column("quote_note", sa.Text(), nullable=True),
        sa.Column("quote_hash", sa.String(length=64), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "method IS NULL OR method IN ('collection', 'delivery')", name="handover_method_valid"
        ),
        sa.CheckConstraint(
            "status IN ('awaiting_choice', 'delivery_requested', 'quote_ready', 'confirmed', 'out_for_delivery', 'collected', 'delivered')",
            name="handover_status_valid",
        ),
        sa.CheckConstraint(
            "delivery_fee_cents IS NULL OR delivery_fee_cents >= 0", name="handover_fee_nonnegative"
        ),
        sa.CheckConstraint("version > 0", name="handover_version_positive"),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["order_revisions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
    )
    op.create_index(op.f("ix_handovers_workspace_id"), "handovers", ["workspace_id"], unique=False)
    op.create_table(
        "handover_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("handover_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["handover_id"],
            ["handovers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        op.f("ix_handover_links_handover_id"), "handover_links", ["handover_id"], unique=False
    )
    op.add_column(
        "workspaces", sa.Column("handover_settings", sa.JSON(), server_default="{}", nullable=False)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("workspaces", "handover_settings")
    op.drop_index(op.f("ix_handover_links_handover_id"), table_name="handover_links")
    op.drop_table("handover_links")
    op.drop_index(op.f("ix_handovers_workspace_id"), table_name="handovers")
    op.drop_table("handovers")
