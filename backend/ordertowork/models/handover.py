"""Fulfillment choices and customer capabilities, separate from production consent."""

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ordertowork.db import Base, new_id, utcnow


class Handover(Base):
    __tablename__ = "handovers"
    __table_args__ = (
        CheckConstraint("version > 0", name="handover_version_positive"),
        CheckConstraint(
            "delivery_fee_cents IS NULL OR delivery_fee_cents >= 0",
            name="handover_fee_nonnegative",
        ),
        CheckConstraint(
            "status IN ('awaiting_choice', 'delivery_requested', 'quote_ready', 'confirmed', "
            "'out_for_delivery', 'collected', 'delivered')",
            name="handover_status_valid",
        ),
        CheckConstraint(
            "method IS NULL OR method IN ('collection', 'delivery')", name="handover_method_valid"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("order_revisions.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="awaiting_choice")
    config: Mapped[dict] = mapped_column(JSON)
    method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    collection_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    customer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_fee_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    quote_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ready_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HandoverLink(Base):
    __tablename__ = "handover_links"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    handover_id: Mapped[str] = mapped_column(ForeignKey("handovers.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
