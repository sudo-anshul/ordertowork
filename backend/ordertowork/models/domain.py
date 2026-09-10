"""Persistent order facts. Agent proposals are distinct from accepted commitments."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ordertowork.db import Base, new_id, utcnow


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(140))
    profile: Mapped[str] = mapped_column(String(30))
    unit_price_cents: Mapped[int] = mapped_column(Integer)
    rush_fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    capacity_units_per_item: Mapped[int] = mapped_column(Integer, default=1)
    variants: Mapped[list] = mapped_column(JSON, default=list)
    sizes: Mapped[list] = mapped_column(JSON, default=list)
    specification: Mapped[dict] = mapped_column(JSON, default=dict)
    lead_days: Mapped[int] = mapped_column(Integer, default=2)
    __table_args__ = (
        CheckConstraint(
            "unit_price_cents >= 0 AND rush_fee_cents >= 0 AND capacity_units_per_item > 0"
        ),
    )


class Resource(Base):
    __tablename__ = "resources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    key: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    label: Mapped[str] = mapped_column(String(150))
    unit: Mapped[str] = mapped_column(String(40))
    total: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    __table_args__ = (UniqueConstraint("workspace_id", "key"), CheckConstraint("total >= 0"))


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    number: Mapped[str] = mapped_column(String(40))
    customer_name: Mapped[str] = mapped_column(String(140))
    customer_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    accepted_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    shared_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    production_status: Mapped[str] = mapped_column(String(20), default="not_started")
    needs_attention: Mapped[bool] = mapped_column(Boolean, default=True)
    hold_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    latest_analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("workspace_id", "number"),)


class OrderRevision(Base):
    __tablename__ = "order_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    base_accepted_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    label: Mapped[str] = mapped_column(String(160))
    terms: Mapped[dict] = mapped_column(JSON)
    terms_hash: Mapped[str] = mapped_column(String(64))
    resource_requirements: Mapped[dict] = mapped_column(JSON, default=dict)
    feasible: Mapped[bool] = mapped_column(Boolean)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("order_id", "number"),)


class Reservation(Base):
    __tablename__ = "reservations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("order_revisions.id"))
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="held")
    __table_args__ = (
        UniqueConstraint("order_id", "revision_id", "resource_id"),
        CheckConstraint("quantity > 0"),
    )


class ApprovalLink(Base):
    __tablename__ = "approval_links"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("order_revisions.id"))
    terms_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Deposit(Base):
    __tablename__ = "deposits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    recorded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("order_id", "idempotency_key"),
        CheckConstraint("amount_cents > 0"),
    )


class SourceMessage(Base):
    __tablename__ = "source_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OrderEvent(Base):
    __tablename__ = "order_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    type: Mapped[str] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
