"""Deterministic commitments and resource operations; models never own these decisions.

The caller commits each operation. PostgreSQL row locks serialize an order and its
resource ledger; no network/inference operation belongs inside these transactions.
"""

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from ordertowork.config import get_settings
from ordertowork.db import new_id, utcnow
from ordertowork.models.core import Workspace
from ordertowork.models.domain import (
    ApprovalLink,
    Deposit,
    Order,
    OrderEvent,
    OrderRevision,
    Product,
    Reservation,
    Resource,
    SourceMessage,
)
from ordertowork.services.demo import demo_workspace_active
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def fail(code: str, message: str, status: int = 409):
    raise HTTPException(status, detail={"code": code, "message": message})


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def iso(value: datetime) -> str:
    return aware(value).isoformat()


def get_order(db: Session, workspace_id: str, order_id: str, *, lock=False) -> Order:
    stmt = select(Order).where(Order.id == order_id, Order.workspace_id == workspace_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    order = db.scalar(stmt)
    if not order:
        fail("not_found", "Order not found.", 404)
    if lock:
        workspace = get_workspace(db, workspace_id)
        if workspace.demo_expires_at is not None:
            events = (
                db.scalar(
                    select(func.count())
                    .select_from(OrderEvent)
                    .where(OrderEvent.order_id == order.id)
                )
                or 0
            )
            if events >= 100:
                fail(
                    "demo_interaction_limit",
                    "This demo order has reached its interaction limit.",
                    429,
                )
    return order


def get_workspace(db: Session, workspace_id: str) -> Workspace:
    workspace = db.get(Workspace, workspace_id)
    if not workspace or workspace.status != "active":
        fail("workspace_unavailable", "Workspace unavailable.", 404)
    if not demo_workspace_active(workspace):
        fail("demo_expired", "This demo has ended. Start a new demo to explore again.", 410)
    return workspace


def get_product(db: Session, workspace_id: str, product_id: str) -> Product:
    product = db.scalar(
        select(Product).where(Product.id == product_id, Product.workspace_id == workspace_id)
    )
    if not product:
        fail("product_not_found", "Choose a product in this business.", 422)
    return product


def revisions_for(db: Session, order: Order) -> list[OrderRevision]:
    return list(
        db.scalars(
            select(OrderRevision)
            .where(
                OrderRevision.order_id == order.id, OrderRevision.workspace_id == order.workspace_id
            )
            .order_by(OrderRevision.number)
        )
    )


def event(db: Session, order: Order, kind: str, message: str, details=None):
    db.add(
        OrderEvent(
            workspace_id=order.workspace_id,
            order_id=order.id,
            type=kind,
            message=message,
            details=details or {},
        )
    )


def paid_cents(db: Session, order: Order) -> int:
    return (
        db.scalar(
            select(func.coalesce(func.sum(Deposit.amount_cents), 0)).where(
                Deposit.order_id == order.id, Deposit.workspace_id == order.workspace_id
            )
        )
        or 0
    )


def reserved(db: Session, resource_id: str, excluding_order_id: str | None = None) -> int:
    stmt = select(func.coalesce(func.sum(Reservation.quantity), 0)).where(
        Reservation.resource_id == resource_id, Reservation.status.in_(("held", "consumed"))
    )
    if excluding_order_id:
        stmt = stmt.where(Reservation.order_id != excluding_order_id)
    return db.scalar(stmt) or 0


def product_dict(product: Product) -> dict:
    return {
        k: getattr(product, k)
        for k in (
            "id",
            "name",
            "profile",
            "unit_price_cents",
            "rush_fee_cents",
            "capacity_units_per_item",
            "variants",
            "sizes",
            "specification",
            "lead_days",
        )
    }


def resource_dict(db: Session, resource: Resource) -> dict:
    used = reserved(db, resource.id)
    return {
        "id": resource.id,
        "key": resource.key,
        "kind": resource.kind,
        "label": resource.label,
        "unit": resource.unit,
        "total": resource.total,
        "reserved": used,
        "available": resource.total - used,
        "metadata": resource.details,
    }


def resources_snapshot(db: Session, workspace_id: str) -> dict:
    return {
        "resources": [
            resource_dict(db, r)
            for r in db.scalars(
                select(Resource)
                .where(Resource.workspace_id == workspace_id)
                .order_by(Resource.kind, Resource.key)
            )
        ],
        "products": [
            product_dict(p)
            for p in db.scalars(
                select(Product).where(Product.workspace_id == workspace_id).order_by(Product.name)
            )
        ],
    }


def normalize_terms(db: Session, order: Order, values: dict) -> tuple[dict, dict]:
    workspace = get_workspace(db, order.workspace_id)
    prior = revisions_for(db, order)
    baseline = next(
        (r for r in prior if r.id == order.accepted_revision_id), prior[0] if prior else None
    )
    product_id = values.get("product_id") or (baseline.terms["product_id"] if baseline else None)
    product = get_product(db, order.workspace_id, product_id or "")
    quantity = values.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 10000:
        fail("invalid_quantity", "Quantity must be an integer from 1 to 10,000.", 422)
    variant = str(values.get("variant", "")).strip().lower()
    if variant not in product.variants:
        fail("invalid_variant", f"Choose an available variant: {', '.join(product.variants)}.", 422)
    sizes = values.get("sizes") or {}
    if not isinstance(sizes, dict) or any(
        k not in product.sizes or isinstance(v, bool) or not isinstance(v, int) or v < 0
        for k, v in sizes.items()
    ):
        fail(
            "invalid_sizes",
            "Sizes must contain configured names and nonnegative whole quantities.",
            422,
        )
    if product.sizes and sum(sizes.values()) != quantity:
        fail("size_total_mismatch", "Size quantities must add up to the order quantity.", 422)
    try:
        pickup = values.get("pickup_at")
        pickup = (
            pickup
            if isinstance(pickup, datetime)
            else datetime.fromisoformat(str(pickup).replace("Z", "+00:00"))
        )
        if pickup.tzinfo is None:
            fail("pickup_timezone_required", "Pickup time must include a timezone offset.", 422)
        local_pickup = pickup.astimezone(ZoneInfo(workspace.timezone))
    except (ValueError, TypeError):
        fail("invalid_pickup", "Enter a valid pickup date and time with a timezone offset.", 422)
    if pickup < utcnow() and not order.is_demo:
        fail("pickup_in_past", "Pickup time must be in the future.", 422)
    specification = dict(product.specification)
    if baseline and baseline.terms["product_id"] == product.id:
        specification.update(baseline.terms.get("specification", {}))
    overrides = values.get("specification") or {}
    if (
        not isinstance(overrides, dict)
        or len(overrides) > 20
        or any(
            not isinstance(k, str) or len(k) > 80 or not isinstance(v, str) or len(v) > 500
            for k, v in overrides.items()
        )
    ):
        fail("invalid_specification", "Specification must contain short named text values.", 422)
    specification.update(overrides)
    if prior:
        original_date = (
            datetime.fromisoformat(prior[0].terms["pickup_at"])
            .astimezone(ZoneInfo(workspace.timezone))
            .date()
        )
    else:
        original_date = utcnow().astimezone(ZoneInfo(workspace.timezone)).date() + timedelta(
            days=product.lead_days
        )
    rush = product.rush_fee_cents if local_pickup.date() < original_date else 0
    subtotal = quantity * product.unit_price_cents
    total = subtotal + rush
    terms = {
        "product_id": product.id,
        "product_name": product.name,
        "quantity": quantity,
        "variant": variant,
        "sizes": sizes,
        "pickup_at": local_pickup.isoformat(),
        "unit_price_cents": product.unit_price_cents,
        "subtotal_cents": subtotal,
        "rush_fee_cents": rush,
        "total_cents": total,
        "required_deposit_cents": (total * workspace.deposit_bps + 9999) // 10000,
        "currency": workspace.currency,
        "timezone": workspace.timezone,
        "deposit_bps": workspace.deposit_bps,
        "specification": specification,
    }
    requirements = {
        f"capacity:{local_pickup.date().isoformat()}": quantity * product.capacity_units_per_item
    }
    if product.sizes:
        requirements.update(
            {
                f"stock:{product.id}:{variant}:{size}": count
                for size, count in sizes.items()
                if count
            }
        )
    else:
        requirements[f"stock:{product.id}:{variant}:unit"] = quantity
    return terms, requirements


def availability(db: Session, order: Order, requirements: dict, *, lock=False) -> tuple[list, dict]:
    keys = list(requirements)
    if lock:
        # Include existing holds in the globally sorted lock set before releasing any.
        old_ids = list(
            db.scalars(
                select(Reservation.resource_id).where(
                    Reservation.order_id == order.id,
                    Reservation.workspace_id == order.workspace_id,
                    Reservation.status == "held",
                )
            )
        )
        stmt = (
            select(Resource)
            .where(
                Resource.workspace_id == order.workspace_id,
                (Resource.key.in_(keys) | Resource.id.in_(old_ids)),
            )
            .order_by(Resource.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    else:
        stmt = select(Resource).where(
            Resource.workspace_id == order.workspace_id, Resource.key.in_(keys)
        )
    resources = {r.key: r for r in db.scalars(stmt)}
    issues = []
    for key, needed in requirements.items():
        resource = resources.get(key)
        available = resource.total - reserved(db, resource.id, order.id) if resource else 0
        if not resource or available < needed:
            issues.append(
                {
                    "code": "missing_resource" if not resource else f"{resource.kind}_shortage",
                    "message": f"{resource.label if resource else key}: needs {needed}; {available} available to this order.",
                    "resource_id": resource.id if resource else None,
                    "required": needed,
                    "available": available,
                }
            )
    return issues, resources


def revision_hash(
    order_id: str,
    number: int,
    terms: dict,
    requirements: dict,
    base_accepted_revision_id: str | None,
) -> str:
    payload = {
        "order_id": order_id,
        "revision": number,
        "terms": terms,
        "requirements": requirements,
        "base_accepted_revision_id": base_accepted_revision_id,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def create_proposal(db: Session, workspace_id: str, order_id: str, values: dict) -> OrderRevision:
    order = get_order(db, workspace_id, order_id, lock=True)
    if order.production_status == "started":
        fail(
            "production_started",
            "Production has started. The owner must resolve committed work before a change can be accepted.",
        )
    terms, requirements = normalize_terms(db, order, values)
    issues, _ = availability(db, order, requirements)
    number = (
        db.scalar(select(func.max(OrderRevision.number)).where(OrderRevision.order_id == order.id))
        or 0
    ) + 1
    digest = revision_hash(order.id, number, terms, requirements, order.accepted_revision_id)
    proposal = OrderRevision(
        workspace_id=workspace_id,
        order_id=order.id,
        number=number,
        base_accepted_revision_id=order.accepted_revision_id,
        label=str(values.get("label") or "Proposed order")[:160],
        terms=terms,
        terms_hash=digest,
        resource_requirements=requirements,
        feasible=not issues,
        issues=issues,
    )
    db.add(proposal)
    db.flush()
    event(
        db,
        order,
        "proposal_created",
        f"Revision {number} prepared. Existing commitments are unchanged.",
    )
    return proposal


def create_order(
    db: Session, workspace_id: str, values: dict, *, is_demo=False, number=None
) -> Order:
    workspace = get_workspace(db, workspace_id)
    is_demo = is_demo or workspace.is_demo
    order = Order(
        id=new_id(),
        workspace_id=workspace_id,
        number=number or f"OT-{secrets.token_hex(4).upper()}",
        customer_name=values["customer_name"].strip(),
        customer_email=values.get("customer_email") or None,
        is_demo=is_demo,
    )
    db.add(order)
    db.flush()
    event(
        db,
        order,
        "order_created",
        "Sample order created."
        if is_demo
        else "Order created; customer acceptance is still required.",
    )
    create_proposal(db, workspace_id, order.id, {**values, "label": "Initial order"})
    return order


def revision_dict(revision: OrderRevision) -> dict:
    return {
        "id": revision.id,
        "number": revision.number,
        "base_accepted_revision_id": revision.base_accepted_revision_id,
        "status": revision.status,
        "label": revision.label,
        "terms": revision.terms,
        "terms_hash": revision.terms_hash,
        "feasible": revision.feasible,
        "issues": revision.issues,
        "created_at": iso(revision.created_at),
    }


def reservation_dicts(db: Session, order: Order) -> list:
    rows = db.execute(
        select(Reservation, Resource)
        .join(Resource, Resource.id == Reservation.resource_id)
        .where(
            Reservation.order_id == order.id,
            Reservation.workspace_id == order.workspace_id,
            Reservation.status.in_(("held", "consumed")),
        )
    ).all()
    return [
        {
            "resource_id": r.resource_id,
            "label": resource.label,
            "quantity": r.quantity,
            "unit": resource.unit,
        }
        for r, resource in rows
    ]


def production_blockers(db: Session, order: Order) -> list[str]:
    reasons = []
    revision = (
        db.get(OrderRevision, order.accepted_revision_id) if order.accepted_revision_id else None
    )
    if not revision:
        reasons.append("Customer approval is required.")
    else:
        if paid_cents(db, order) < revision.terms["required_deposit_cents"]:
            reasons.append("Required deposit has not been recorded.")
        rows = db.execute(
            select(Reservation, Resource)
            .join(Resource, Resource.id == Reservation.resource_id)
            .where(
                Reservation.order_id == order.id,
                Reservation.workspace_id == order.workspace_id,
                Reservation.revision_id == revision.id,
                Reservation.status.in_(("held", "consumed")),
            )
        ).all()
        actual = {resource.key: reservation.quantity for reservation, resource in rows}
        if actual != revision.resource_requirements:
            reasons.append("Required resources are not reserved for the accepted revision.")
    if order.hold_reason:
        reasons.append(order.hold_reason)
    return reasons


def order_summary(db: Session, order: Order) -> dict:
    revision = (
        db.get(OrderRevision, order.accepted_revision_id) if order.accepted_revision_id else None
    )
    paid = paid_cents(db, order)
    if order.production_status == "started":
        status = "in_production"
    elif order.hold_reason:
        status = "on_hold"
    elif order.shared_revision_id:
        status = "awaiting_approval"
    elif order.needs_attention or db.scalar(
        select(OrderRevision.id)
        .where(OrderRevision.order_id == order.id, OrderRevision.status == "proposed")
        .limit(1)
    ):
        status = "needs_review"
    elif revision and paid < revision.terms["required_deposit_cents"]:
        status = "deposit_due"
    elif revision and not production_blockers(db, order):
        status = "ready"
    else:
        status = "needs_review" if order.latest_analysis else "new"
    return {
        "id": order.id,
        "number": order.number,
        "customer_name": order.customer_name,
        "customer_email": order.customer_email,
        "is_demo": order.is_demo,
        "status": status,
        "production_status": order.production_status,
        "hold_reason": order.hold_reason,
        "accepted_revision": revision_dict(revision) if revision else None,
        "deposit_paid_cents": paid,
        "created_at": iso(order.created_at),
    }


def message_dict(message: SourceMessage) -> dict:
    return {
        "id": message.id,
        "body": message.body,
        "source": message.source,
        "created_at": iso(message.created_at),
    }


def order_detail(db: Session, workspace_id: str, order_id: str) -> dict:
    order = get_order(db, workspace_id, order_id)
    db.flush()
    blockers = production_blockers(db, order)
    from ordertowork.models.jobs import Job

    job = db.scalar(
        select(Job)
        .where(Job.order_id == order.id, Job.workspace_id == workspace_id)
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    latest_job = {"id": job.id, "status": job.status, "error": job.error} if job else None
    return {
        "latest_job": latest_job,
        **order_summary(db, order),
        "revisions": [revision_dict(r) for r in revisions_for(db, order)],
        "messages": [
            message_dict(m)
            for m in db.scalars(
                select(SourceMessage)
                .where(
                    SourceMessage.order_id == order.id, SourceMessage.workspace_id == workspace_id
                )
                .order_by(SourceMessage.created_at)
            )
        ],
        "events": [
            {"id": e.id, "type": e.type, "message": e.message, "created_at": iso(e.created_at)}
            for e in db.scalars(
                select(OrderEvent)
                .where(OrderEvent.order_id == order.id, OrderEvent.workspace_id == workspace_id)
                .order_by(OrderEvent.created_at.desc())
            )
        ],
        "reservations": reservation_dicts(db, order),
        "production_ready": not blockers,
        "production_blockers": blockers,
        "shared_revision_id": order.shared_revision_id,
        "latest_analysis": order.latest_analysis,
    }


def preview_change(db: Session, workspace_id: str, order_id: str, request: dict) -> dict:
    """Read-only pricing and availability preview for a trusted agent tool."""
    order = get_order(db, workspace_id, order_id)
    terms, requirements = normalize_terms(db, order, request)
    issues, _ = availability(db, order, requirements)
    return {"terms": terms, "issues": issues, "feasible": not issues}


def order_snapshot(db: Session, workspace_id: str, order_id: str) -> dict:
    workspace = get_workspace(db, workspace_id)
    return {
        **order_detail(db, workspace_id, order_id),
        **resources_snapshot(db, workspace_id),
        "workspace": {
            "id": workspace.id,
            "name": workspace.name,
            "profile": workspace.profile,
            "currency": workspace.currency,
            "timezone": workspace.timezone,
            "deposit_bps": workspace.deposit_bps,
            "is_demo": workspace.is_demo,
        },
    }


def record_message(
    db: Session, workspace_id: str, order_id: str, body: str, source="manual"
) -> SourceMessage:
    order = get_order(db, workspace_id, order_id, lock=True)
    workspace = get_workspace(db, workspace_id)
    if workspace.demo_expires_at is not None:
        count = (
            db.scalar(
                select(func.count())
                .select_from(SourceMessage)
                .where(SourceMessage.order_id == order_id)
            )
            or 0
        )
        if count >= 10:
            fail("demo_message_limit", "This demo order has reached its message limit.", 429)
    if not body.strip() or len(body) > 12000:
        fail("invalid_message", "Message must contain 1–12,000 characters.", 422)
    message = SourceMessage(
        workspace_id=workspace_id, order_id=order.id, body=body.strip(), source=source
    )
    db.add(message)
    db.flush()
    order.needs_attention = True
    event(
        db, order, "message_received", "Customer request recorded; it does not authorize changes."
    )
    return message


def analyze_change(db: Session, workspace_id: str, order_id: str, request: dict) -> dict:
    """Create deterministic alternatives from model-extracted facts, never acceptance."""
    order = get_order(db, workspace_id, order_id, lock=True)
    order.needs_attention = True
    missing = list(request.get("missing_fields") or [])
    intent = request.get("intent") or "unknown"
    order.latest_analysis = {
        "intent": intent,
        "evidence": request.get("evidence") or [],
        "missing_fields": missing,
        "requested_issues": [],
    }
    if order.production_status == "started":
        event(
            db,
            order,
            "owner_resolution_required",
            "The request concerns work already started; automatic changes are disabled.",
        )
        return order_detail(db, workspace_id, order_id)
    if intent not in ("change_request", "new_order") or missing:
        event(
            db,
            order,
            "clarification_required",
            "Review the source and clarify missing details. No approval or payment was inferred.",
        )
        return order_detail(db, workspace_id, order_id)
    baseline = next(
        (r for r in revisions_for(db, order) if r.id == order.accepted_revision_id), None
    )
    if not baseline:
        candidates = revisions_for(db, order)
        baseline = candidates[0] if candidates else None
    values = {
        **(baseline.terms if baseline else {}),
        **{k: v for k, v in request.items() if v is not None},
        "label": "Requested change",
    }
    try:
        terms, requirements = normalize_terms(db, order, values)
    except HTTPException as exc:
        order.latest_analysis = {**order.latest_analysis, "missing_fields": [exc.detail["message"]]}
        event(db, order, "clarification_required", exc.detail["message"])
        return order_detail(db, workspace_id, order_id)
    issues, _ = availability(db, order, requirements)
    order.latest_analysis = {**order.latest_analysis, "requested_issues": issues}
    # New analysis retires only unshared suggestions; a customer's open link is
    # replaced only by an explicit owner share or customer request-change action.
    for revision in revisions_for(db, order):
        if revision.status == "proposed":
            revision.status = "superseded"
    requested = create_proposal(db, workspace_id, order_id, values)
    if not issues:
        requested.label = "Requested change fits"
    elif baseline:
        product = get_product(db, workspace_id, terms["product_id"])
        seen = {json.dumps(terms, sort_keys=True)}
        alternatives = [
            {
                **values,
                "pickup_at": baseline.terms["pickup_at"],
                "label": "Keep the requested quantity",
            }
        ]
        alternatives.extend(
            {
                **values,
                "variant": variant,
                "pickup_at": baseline.terms["pickup_at"],
                "label": f"Keep quantity · {variant.title()} · original pickup",
            }
            for variant in product.variants
            if variant != terms["variant"]
        )
        alternatives.append(
            {
                **values,
                "quantity": baseline.terms["quantity"],
                "variant": baseline.terms["variant"],
                "sizes": baseline.terms["sizes"],
                "specification": baseline.terms.get("specification", {}),
                "label": "Keep the earlier pickup",
            }
        )
        for alternative in alternatives:
            option_terms, option_requirements = normalize_terms(db, order, alternative)
            key = json.dumps(option_terms, sort_keys=True)
            option_issues, _ = availability(db, order, option_requirements)
            if not option_issues and key not in seen:
                create_proposal(db, workspace_id, order_id, alternative)
                seen.add(key)
    event(
        db,
        order,
        "analysis_completed",
        "Options checked against configured prices, stock and capacity. Owner review is required.",
    )
    return order_detail(db, workspace_id, order_id)


def share_proposal(db: Session, workspace_id: str, order_id: str, revision_id: str) -> dict:
    order = get_order(db, workspace_id, order_id, lock=True)
    if order.production_status == "started":
        fail(
            "production_started", "Production has started; an owner must resolve the existing work."
        )
    revision = db.scalar(
        select(OrderRevision).where(
            OrderRevision.id == revision_id,
            OrderRevision.order_id == order.id,
            OrderRevision.workspace_id == workspace_id,
        )
    )
    if not revision or revision.status not in ("proposed", "shared"):
        fail("invalid_proposal", "Choose a current proposed revision.")
    if revision.base_accepted_revision_id != order.accepted_revision_id:
        fail("stale_revision", "The accepted agreement changed. Prepare a fresh proposal.")
    if not hmac.compare_digest(
        revision.terms_hash,
        revision_hash(
            order.id,
            revision.number,
            revision.terms,
            revision.resource_requirements,
            revision.base_accepted_revision_id,
        ),
    ):
        fail(
            "terms_mismatch", "Proposal terms changed after preparation. Prepare a fresh proposal."
        )
    issues, _ = availability(db, order, revision.resource_requirements)
    if issues:
        fail(
            "infeasible_proposal",
            "This option no longer fits configured stock or capacity. Prepare another option.",
        )
    for link in db.scalars(
        select(ApprovalLink).where(
            ApprovalLink.order_id == order.id, ApprovalLink.status == "pending"
        )
    ):
        link.status = "revoked"
    for candidate in revisions_for(db, order):
        if candidate.status == "shared" and candidate.id != revision.id:
            candidate.status = "superseded"
    token = secrets.token_urlsafe(32)
    expires = utcnow() + timedelta(hours=72)
    workspace = get_workspace(db, workspace_id)
    if workspace.demo_expires_at is not None:
        expires = min(expires, aware(workspace.demo_expires_at))
    db.add(
        ApprovalLink(
            workspace_id=workspace_id,
            order_id=order.id,
            revision_id=revision.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            terms_hash=revision.terms_hash,
            expires_at=expires,
        )
    )
    revision.status = "shared"
    revision.feasible = True
    revision.issues = []
    order.shared_revision_id = revision.id
    order.needs_attention = False
    event(
        db,
        order,
        "proposal_shared",
        f"Approval link created for exact revision {revision.number}; expires {iso(expires)}.",
    )
    db.flush()
    return {
        "url": f"{get_settings().app_url.rstrip('/')}/customer/{token}",
        "expires_at": iso(expires),
        "revision_id": revision.id,
        "terms_hash": revision.terms_hash,
    }


def _public_context(
    db: Session, token: str, *, lock=False
) -> tuple[ApprovalLink, Order, OrderRevision, Workspace]:
    if not 40 <= len(token) <= 100:
        fail("link_unavailable", "This approval link is unavailable or expired.", 410)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    link = db.scalar(select(ApprovalLink).where(ApprovalLink.token_hash == token_hash))
    if not link:
        fail("link_unavailable", "This approval link is unavailable or expired.", 410)
    # Always lock order before link/resources to agree with all owner operations.
    order = get_order(db, link.workspace_id, link.order_id, lock=lock)
    if lock:
        link = db.scalar(
            select(ApprovalLink)
            .where(ApprovalLink.id == link.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    workspace = get_workspace(db, order.workspace_id)
    if aware(link.expires_at) <= utcnow() or link.status not in ("pending", "approved"):
        fail(
            "link_unavailable",
            "This approval link is unavailable or expired. Ask the business for a current proposal.",
            410,
        )
    current = order.accepted_revision_id if link.status == "approved" else order.shared_revision_id
    if current != link.revision_id:
        fail(
            "stale_revision",
            "A newer proposal has replaced this link. Ask the business for the current version.",
            410,
        )
    revision = db.scalar(
        select(OrderRevision).where(
            OrderRevision.id == link.revision_id,
            OrderRevision.workspace_id == order.workspace_id,
            OrderRevision.order_id == order.id,
        )
    )
    if not revision or not hmac.compare_digest(link.terms_hash, revision.terms_hash):
        fail("stale_revision", "The proposal is no longer available.", 410)
    expected_hash = revision_hash(
        order.id,
        revision.number,
        revision.terms,
        revision.resource_requirements,
        revision.base_accepted_revision_id,
    )
    if not hmac.compare_digest(link.terms_hash, expected_hash):
        fail(
            "terms_mismatch",
            "The proposal changed after sharing. Ask the business for a fresh link.",
            410,
        )
    if (
        link.status == "pending"
        and revision.base_accepted_revision_id != order.accepted_revision_id
    ):
        fail(
            "stale_revision",
            "The accepted agreement changed. Ask the business for a fresh proposal.",
            410,
        )
    return link, order, revision, workspace


def customer_view(db: Session, token: str) -> dict:
    link, order, revision, workspace = _public_context(db, token)
    prior = next(
        (
            r
            for r in reversed(revisions_for(db, order))
            if r.number < revision.number and r.status in ("accepted", "superseded_accepted")
        ),
        None,
    )
    paid = paid_cents(db, order)
    # Internal availability and evidence are deliberately excluded.
    public_revision = revision_dict(revision)
    public_revision["issues"] = []
    return {
        "business": {
            "name": workspace.name,
            "currency": revision.terms["currency"],
            "timezone": revision.terms["timezone"],
        },
        "order": {
            "number": order.number,
            "customer_name": order.customer_name,
            "is_demo": order.is_demo,
        },
        "revision": public_revision,
        "previous_terms": prior.terms if prior else None,
        "deposit_paid_cents": paid,
        "top_up_cents": max(0, revision.terms["required_deposit_cents"] - paid),
        "balance_after_deposit_cents": max(
            0, revision.terms["total_cents"] - max(paid, revision.terms["required_deposit_cents"])
        ),
        "expires_at": iso(link.expires_at),
        "status": "approved" if link.status == "approved" else "pending",
        "approval_mode": "bearer_link",
    }


def approve_customer(db: Session, token: str, terms_hash: str, consent: bool) -> dict:
    if consent is not True:
        fail("consent_required", "Review and explicitly approve the displayed revision.", 422)
    link, order, revision, _ = _public_context(db, token, lock=True)
    if not hmac.compare_digest(terms_hash, link.terms_hash):
        fail("terms_mismatch", "The displayed proposal does not match this approval link.")
    result = {
        "revision_id": revision.id,
        "order_number": order.number,
        "required_deposit_cents": revision.terms["required_deposit_cents"],
        "top_up_cents": max(0, revision.terms["required_deposit_cents"] - paid_cents(db, order)),
    }
    if link.status == "approved":
        return {"status": "already_approved", **result}
    if order.production_status == "started":
        fail(
            "production_started",
            "The business has started production. Please contact them about this change.",
        )
    issues, resources = availability(db, order, revision.resource_requirements, lock=True)
    if issues:
        # Safe diagnostic updates only; endpoint persists these before its 409.
        revision.status = "blocked"
        revision.feasible = False
        revision.issues = issues
        link.status = "revoked"
        order.shared_revision_id = None
        order.needs_attention = True
        event(
            db,
            order,
            "availability_changed",
            "Approval could not complete because availability changed. Previous agreement and holds were preserved.",
        )
        fail(
            "availability_changed",
            "Availability changed before approval. Your previous order is unchanged; the business must prepare another option.",
        )
    # Every stock/capacity row is now locked. Release and reserve in one transaction.
    for reservation in db.scalars(
        select(Reservation).where(
            Reservation.order_id == order.id,
            Reservation.workspace_id == order.workspace_id,
            Reservation.status == "held",
        )
    ):
        reservation.status = "released"
    for key, quantity in revision.resource_requirements.items():
        db.add(
            Reservation(
                workspace_id=order.workspace_id,
                order_id=order.id,
                revision_id=revision.id,
                resource_id=resources[key].id,
                quantity=quantity,
            )
        )
    for candidate in revisions_for(db, order):
        if candidate.id != revision.id:
            if candidate.status == "accepted":
                candidate.status = "superseded_accepted"
            elif candidate.status in ("shared", "proposed"):
                candidate.status = "superseded"
    for other_link in db.scalars(
        select(ApprovalLink).where(
            ApprovalLink.order_id == order.id,
            ApprovalLink.id != link.id,
            ApprovalLink.status == "pending",
        )
    ):
        other_link.status = "revoked"
    revision.status = "accepted"
    order.accepted_revision_id = revision.id
    order.shared_revision_id = None
    order.needs_attention = False
    link.status = "approved"
    link.approved_at = utcnow()
    event(
        db,
        order,
        "customer_approved",
        f"Customer approved revision {revision.number}. Stock and capacity were reserved for this revision.",
        {
            "revision_id": revision.id,
            "terms_hash": revision.terms_hash,
            "approval_mode": "bearer_link",
        },
    )
    db.flush()
    return {"status": "approved", **result}


def request_customer_change(db: Session, token: str, body: str) -> dict:
    link, order, revision, _ = _public_context(db, token, lock=True)
    if link.status != "pending":
        fail(
            "already_approved",
            "This proposal has already been approved. Contact the business for a further change.",
        )
    record_message(db, order.workspace_id, order.id, body, source="customer_link")
    link.status = "revoked"
    revision.status = "change_requested"
    order.shared_revision_id = None
    order.needs_attention = True
    event(
        db,
        order,
        "customer_change_requested",
        "Customer requested a change; previous accepted terms and reservations remain intact.",
    )
    return {"status": "change_requested"}


def record_deposit(
    db: Session,
    workspace_id: str,
    order_id: str,
    amount_cents: int,
    reference: str,
    idempotency_key: str,
    user_id: str | None,
) -> dict:
    order = get_order(db, workspace_id, order_id, lock=True)
    if (
        isinstance(amount_cents, bool)
        or not isinstance(amount_cents, int)
        or not 1 <= amount_cents <= 100000000
    ):
        fail("invalid_deposit", "Recorded amount must be positive integer cents.", 422)
    if not reference.strip() or not idempotency_key.strip():
        fail("invalid_deposit", "A payment reference and idempotency key are required.", 422)
    existing = db.scalar(
        select(Deposit).where(
            Deposit.order_id == order.id, Deposit.idempotency_key == idempotency_key
        )
    )
    if existing:
        if existing.amount_cents != amount_cents or existing.reference != reference.strip():
            fail(
                "idempotency_conflict",
                "This request key was already used for a different payment record.",
            )
        return order_detail(db, workspace_id, order_id)
    db.add(
        Deposit(
            workspace_id=workspace_id,
            order_id=order.id,
            amount_cents=amount_cents,
            reference=reference.strip(),
            idempotency_key=idempotency_key,
            recorded_by=user_id,
        )
    )
    event(
        db,
        order,
        "deposit_recorded",
        f"Recorded a received deposit of {get_workspace(db, workspace_id).currency} "
        f"{amount_cents // 100:,}.{amount_cents % 100:02d}.",
        {"recorded_by": user_id, "reference": reference.strip()},
    )
    db.flush()
    return order_detail(db, workspace_id, order_id)


def production_ticket(db: Session, workspace_id: str, order_id: str) -> dict:
    order = get_order(db, workspace_id, order_id)
    blockers = production_blockers(db, order)
    if blockers:
        fail("production_blocked", " ".join(blockers))
    revision = db.get(OrderRevision, order.accepted_revision_id)
    paid = paid_cents(db, order)
    return {
        "number": order.number,
        "customer_name": order.customer_name,
        "revision": revision.number,
        "terms": revision.terms,
        "deposit_paid_cents": paid,
        "balance_cents": max(0, revision.terms["total_cents"] - paid),
        "reservations": reservation_dicts(db, order),
        "is_demo": order.is_demo,
        "production_status": order.production_status,
    }


def start_production(db: Session, workspace_id: str, order_id: str, expected_revision: int) -> dict:
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 1
    ):
        fail(
            "invalid_revision",
            "Supply the revision number displayed on the production ticket.",
            422,
        )
    order = get_order(db, workspace_id, order_id, lock=True)
    accepted = (
        db.get(OrderRevision, order.accepted_revision_id) if order.accepted_revision_id else None
    )
    if not accepted or accepted.number != expected_revision:
        fail(
            "stale_revision",
            "The accepted revision changed. Reload and review the current production ticket before starting work.",
        )
    production_ticket(db, workspace_id, order_id)
    if order.production_status != "started":
        order.production_status = "started"
        for reservation in db.scalars(
            select(Reservation).where(
                Reservation.order_id == order.id, Reservation.status == "held"
            )
        ):
            reservation.status = "consumed"
        for link in db.scalars(
            select(ApprovalLink).where(
                ApprovalLink.order_id == order.id, ApprovalLink.status == "pending"
            )
        ):
            link.status = "revoked"
        order.shared_revision_id = None
        event(
            db,
            order,
            "production_started",
            "Production started. Resources are committed; automatic changes are disabled.",
        )
    return order_detail(db, workspace_id, order_id)


def set_hold(db: Session, workspace_id: str, order_id: str, reason: str | None) -> dict:
    order = get_order(db, workspace_id, order_id, lock=True)
    order.hold_reason = reason.strip() if reason and reason.strip() else None
    event(
        db,
        order,
        "hold_set" if order.hold_reason else "hold_cleared",
        f"Owner set production hold: {order.hold_reason}"
        if order.hold_reason
        else "Owner cleared the production hold.",
    )
    return order_detail(db, workspace_id, order_id)


def update_resource(db: Session, workspace_id: str, resource_id: str, total: int) -> dict:
    if isinstance(total, bool) or not isinstance(total, int) or not 0 <= total <= 100000000:
        fail("invalid_total", "Resource total must be a nonnegative whole number.", 422)
    resource = db.scalar(
        select(Resource)
        .where(Resource.id == resource_id, Resource.workspace_id == workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not resource:
        fail("not_found", "Resource not found.", 404)
    used = reserved(db, resource.id)
    if total < used:
        fail("resource_committed", f"Cannot reduce total below {used} committed {resource.unit}.")
    resource.total = total
    return resource_dict(db, resource)


def operational_ticket(ticket: dict) -> dict:
    """Operator view excludes all commercial terms and payment history."""
    allowed_terms = (
        "product_id",
        "product_name",
        "quantity",
        "variant",
        "sizes",
        "pickup_at",
        "timezone",
        "specification",
    )
    return {
        "number": ticket["number"],
        "customer_name": ticket["customer_name"],
        "revision": ticket["revision"],
        "terms": {k: ticket["terms"][k] for k in allowed_terms},
        "reservations": ticket["reservations"],
        "is_demo": ticket["is_demo"],
        "production_status": ticket["production_status"],
    }


def production_queue(db: Session, workspace_id: str) -> dict:
    results = []
    for order in db.scalars(
        select(Order)
        .where(Order.workspace_id == workspace_id, Order.accepted_revision_id.is_not(None))
        .order_by(Order.created_at)
    ):
        if production_blockers(db, order):
            continue
        revision = db.get(OrderRevision, order.accepted_revision_id)
        results.append(
            {
                "id": order.id,
                "number": order.number,
                "customer_name": order.customer_name,
                "is_demo": order.is_demo,
                "production_status": order.production_status,
                "revision": revision.number,
                **{
                    k: revision.terms[k]
                    for k in (
                        "pickup_at",
                        "product_name",
                        "quantity",
                        "variant",
                        "sizes",
                        "specification",
                    )
                },
            }
        )
    return {"orders": results}
