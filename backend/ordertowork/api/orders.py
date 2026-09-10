"""Tenant-scoped owner operations and narrowly scoped public customer consent."""

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from ordertowork.db import get_db
from ordertowork.models.domain import Order, Product, Resource
from ordertowork.services import orders as service
from ordertowork.services.auth import Actor, get_actor, require_membership
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter()


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProposalInput(StrictInput):
    product_id: str | None = None
    quantity: int = Field(ge=1, le=10000, strict=True)
    variant: str = Field(min_length=1, max_length=80)
    sizes: dict[str, int] = Field(default_factory=dict)
    pickup_at: datetime
    specification: dict[str, str] = Field(default_factory=dict)
    label: str | None = Field(default=None, max_length=160)


class OrderInput(ProposalInput):
    product_id: str
    customer_name: str = Field(min_length=1, max_length=140)
    customer_email: str | None = Field(default=None, max_length=320)

    @field_validator("customer_email")
    @classmethod
    def plausible_email(cls, value):
        if value and ("@" not in value or any(char.isspace() for char in value)):
            raise ValueError("Enter a valid email address")
        return value


class MessageInput(StrictInput):
    body: str = Field(min_length=1, max_length=12000)
    source: Literal["manual"] = "manual"


class DepositInput(StrictInput):
    amount_cents: int = Field(gt=0, le=100000000, strict=True)
    reference: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=100)


class ProductionStartInput(StrictInput):
    expected_revision: int = Field(ge=1, strict=True)


class HoldInput(StrictInput):
    reason: str | None = Field(default=None, max_length=500)


class ResourceUpdate(StrictInput):
    total: int = Field(ge=0, le=100000000, strict=True)


class ResourceInput(ResourceUpdate):
    kind: Literal["stock", "capacity"]
    key: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=150)
    unit: str = Field(min_length=1, max_length=40)
    metadata: dict = Field(default_factory=dict)


class ProductUpdate(StrictInput):
    name: str | None = Field(default=None, min_length=1, max_length=140)
    unit_price_cents: int | None = Field(default=None, ge=0, le=100000000, strict=True)
    rush_fee_cents: int | None = Field(default=None, ge=0, le=100000000, strict=True)
    capacity_units_per_item: int | None = Field(default=None, ge=1, le=10000, strict=True)
    lead_days: int | None = Field(default=None, ge=0, le=365, strict=True)
    specification: dict[str, str] | None = None


class ApprovalInput(StrictInput):
    terms_hash: str = Field(min_length=64, max_length=64)
    consent: bool = Field(strict=True)


class ChangeInput(StrictInput):
    body: str = Field(min_length=1, max_length=12000)


def owner(db, actor, workspace_id):
    return require_membership(db, actor, workspace_id, roles=("owner",))


@router.get("/workspaces/{workspace_id}/orders")
def list_orders(
    workspace_id: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)
):
    owner(db, actor, workspace_id)
    rows = db.scalars(
        select(Order).where(Order.workspace_id == workspace_id).order_by(Order.created_at.desc())
    )
    return {"orders": [service.order_summary(db, row) for row in rows]}


@router.post("/workspaces/{workspace_id}/orders", status_code=201)
def new_order(
    workspace_id: str,
    body: OrderInput,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    order = service.create_order(db, workspace_id, body.model_dump())
    result = service.order_detail(db, workspace_id, order.id)
    db.commit()
    return result


@router.get("/workspaces/{workspace_id}/orders/{order_id}")
def read_order(
    workspace_id: str,
    order_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    return service.order_detail(db, workspace_id, order_id)


@router.post("/workspaces/{workspace_id}/orders/{order_id}/messages", status_code=202)
def add_message(
    workspace_id: str,
    order_id: str,
    body: MessageInput,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    from ordertowork.services.jobs import enqueue_analysis

    message = service.record_message(db, workspace_id, order_id, body.body, body.source)
    job = enqueue_analysis(db, workspace_id, order_id, message.id)
    result = {"message": service.message_dict(message), "job": {"id": job.id, "status": job.status}}
    db.commit()
    return result


@router.post("/workspaces/{workspace_id}/orders/{order_id}/proposals")
def propose(
    workspace_id: str,
    order_id: str,
    body: ProposalInput,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    service.create_proposal(db, workspace_id, order_id, body.model_dump(exclude_none=True))
    result = service.order_detail(db, workspace_id, order_id)
    db.commit()
    return result


@router.post("/workspaces/{workspace_id}/orders/{order_id}/proposals/{revision_id}/share")
def share(
    workspace_id: str,
    order_id: str,
    revision_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    result = service.share_proposal(db, workspace_id, order_id, revision_id)
    db.commit()
    return result


@router.post("/workspaces/{workspace_id}/orders/{order_id}/deposits")
def add_deposit(
    workspace_id: str,
    order_id: str,
    body: DepositInput,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    result = service.record_deposit(
        db,
        workspace_id,
        order_id,
        body.amount_cents,
        body.reference,
        body.idempotency_key,
        actor.user.id,
    )
    db.commit()
    return result


@router.post("/workspaces/{workspace_id}/orders/{order_id}/hold")
def hold(
    workspace_id: str,
    order_id: str,
    body: HoldInput,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    result = service.set_hold(db, workspace_id, order_id, body.reason)
    db.commit()
    return result


@router.get("/workspaces/{workspace_id}/orders/{order_id}/ticket")
def ticket(
    workspace_id: str,
    order_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    membership = require_membership(db, actor, workspace_id)
    result = service.production_ticket(db, workspace_id, order_id)
    return service.operational_ticket(result) if membership.role == "operator" else result


@router.post("/workspaces/{workspace_id}/orders/{order_id}/production/start")
def start(
    workspace_id: str,
    order_id: str,
    body: ProductionStartInput,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    membership = require_membership(db, actor, workspace_id)
    result = service.start_production(db, workspace_id, order_id, body.expected_revision)
    if membership.role == "operator":
        result = service.operational_ticket(service.production_ticket(db, workspace_id, order_id))
    db.commit()
    return result


@router.get("/workspaces/{workspace_id}/resources")
def resources(workspace_id: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    owner(db, actor, workspace_id)
    return service.resources_snapshot(db, workspace_id)


@router.patch("/workspaces/{workspace_id}/resources/{resource_id}")
def change_resource(
    workspace_id: str,
    resource_id: str,
    body: ResourceUpdate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    result = service.update_resource(db, workspace_id, resource_id, body.total)
    db.commit()
    return result


@router.post("/workspaces/{workspace_id}/resources", status_code=201)
def add_resource(
    workspace_id: str,
    body: ResourceInput,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    if db.scalar(
        select(Resource.id).where(Resource.workspace_id == workspace_id, Resource.key == body.key)
    ):
        service.fail("resource_exists", "A resource with that key already exists.")
    if body.kind == "capacity":
        try:
            day = date.fromisoformat(body.key.removeprefix("capacity:"))
            if body.key != f"capacity:{day.isoformat()}":
                raise ValueError
        except ValueError:
            service.fail("invalid_resource_key", "Capacity keys must use capacity:YYYY-MM-DD.", 422)
        metadata = {"date": day.isoformat()}
    else:
        parts = body.key.split(":")
        if len(parts) != 4 or parts[0] != "stock":
            service.fail(
                "invalid_resource_key",
                "Stock key must identify a configured product, variant and size.",
                422,
            )
        product = service.get_product(db, workspace_id, parts[1])
        if parts[2] not in product.variants or parts[3] not in (product.sizes or ["unit"]):
            service.fail(
                "invalid_resource_key",
                "Stock key must identify a configured variant and size.",
                422,
            )
        metadata = {"product_id": product.id, "variant": parts[2], "size": parts[3]}
    resource = Resource(
        workspace_id=workspace_id,
        key=body.key,
        kind=body.kind,
        label=body.label,
        unit=body.unit,
        total=body.total,
        details=metadata,
    )
    db.add(resource)
    db.flush()
    result = service.resource_dict(db, resource)
    db.commit()
    return result


@router.patch("/workspaces/{workspace_id}/products/{product_id}")
def change_product(
    workspace_id: str,
    product_id: str,
    body: ProductUpdate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    product = db.scalar(
        select(Product)
        .where(Product.id == product_id, Product.workspace_id == workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not product:
        service.fail("not_found", "Product not found.", 404)
    values = body.model_dump(exclude_none=True)
    if "specification" in values and (
        len(values["specification"]) > 20
        or any(len(k) > 80 or len(v) > 500 for k, v in values["specification"].items())
    ):
        service.fail(
            "invalid_specification", "Specification must contain short named text values.", 422
        )
    for key, value in values.items():
        setattr(product, key, value)
    result = service.product_dict(product)
    db.commit()
    return result


@router.get("/customer/{token}")
def customer(token: str, db: Session = Depends(get_db)):
    return service.customer_view(db, token)


@router.post("/customer/{token}/approve")
def approve(token: str, body: ApprovalInput, db: Session = Depends(get_db)):
    try:
        result = service.approve_customer(db, token, body.terms_hash, body.consent)
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get("code") == "availability_changed":
            db.commit()  # Only diagnostic state; original commitments were untouched.
        raise
    db.commit()
    return result


@router.post("/customer/{token}/request-change")
def request_change(token: str, body: ChangeInput, db: Session = Depends(get_db)):
    result = service.request_customer_change(db, token, body.body)
    db.commit()
    return result


@router.get("/workspaces/{workspace_id}/production")
def production(workspace_id: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    require_membership(db, actor, workspace_id)
    return service.production_queue(db, workspace_id)
