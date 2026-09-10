"""Explicit business templates and opt-in, clearly marked demonstration data."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ordertowork.db import utcnow
from ordertowork.models.core import Workspace
from ordertowork.models.domain import Deposit, Order, OrderRevision, Product, Reservation, Resource
from ordertowork.services.orders import (
    analyze_change,
    availability,
    create_order,
    event,
    record_message,
)
from sqlalchemy import select
from sqlalchemy.orm import Session


def _resource(
    db: Session,
    workspace: Workspace,
    key: str,
    kind: str,
    label: str,
    unit: str,
    total: int,
    metadata=None,
):
    existing = db.scalar(
        select(Resource).where(Resource.workspace_id == workspace.id, Resource.key == key)
    )
    if existing:
        return existing
    resource = Resource(
        workspace_id=workspace.id,
        key=key,
        kind=kind,
        label=label,
        unit=unit,
        total=total,
        details=metadata or {},
    )
    db.add(resource)
    db.flush()
    return resource


def configure_workspace(db: Session, workspace: Workspace) -> Product:
    """Starter catalogue prices are editable; stock/capacity start at zero.

    No customer, approval, payment or resource availability is invented for real
    workspaces. Owners must configure actual availability before sharing quotes.
    """
    existing = db.scalar(select(Product).where(Product.workspace_id == workspace.id))
    if existing:
        return existing
    bakery = workspace.profile == "bakery"
    product = Product(
        workspace_id=workspace.id,
        profile=workspace.profile,
        name="Vanilla cupcakes" if bakery else "Custom shirts",
        unit_price_cents=400 if bakery else 1800,
        rush_fee_cents=2000 if bakery else 9000,
        capacity_units_per_item=2 if bakery else 1,
        variants=["vanilla"] if bakery else ["navy", "charcoal"],
        sizes=[] if bakery else ["S", "M", "L"],
        specification={},
        lead_days=3 if bakery else 2,
    )
    db.add(product)
    db.flush()
    for variant in product.variants:
        for size in product.sizes or ["unit"]:
            _resource(
                db,
                workspace,
                f"stock:{product.id}:{variant}:{size}",
                "stock",
                f"{variant.title()} {size}" if not bakery else "Vanilla cupcake ingredients",
                "shirts" if not bakery else "portions",
                0,
                {"product_id": product.id, "variant": variant, "size": size},
            )
    today = utcnow().astimezone(ZoneInfo(workspace.timezone)).date()
    for day in (today + timedelta(days=i) for i in range(15)):
        _resource(
            db,
            workspace,
            f"capacity:{day.isoformat()}",
            "capacity",
            f"Production · {day.isoformat()}",
            "prep minutes" if bakery else "shirts",
            0,
            {"date": day.isoformat()},
        )
    return product


def seed_workspace(db: Session, workspace: Workspace, *, relative_dates: bool = False):
    """Seed one synthetic order; guest dates stay usable throughout judging.

    Established development fixtures keep their canonical dates. New guest demos
    use the next relevant weekday at least four days out, so the scenario never
    opens with an overdue pickup. Moving the pickup earlier still carries the
    normal expedited-change fee used in this scenario.
    """
    if db.scalar(select(Order.id).where(Order.workspace_id == workspace.id).limit(1)):
        return
    product = configure_workspace(db, workspace)
    bakery = workspace.profile == "bakery"
    product.specification = (
        {"icing": "Blue", "recipe": "V1"}
        if bakery
        else {"proof": "FN-club-lockup-v1", "placement": "Front", "ink_colors": "1"}
    )
    for resource in db.scalars(
        select(Resource).where(Resource.workspace_id == workspace.id, Resource.kind == "stock")
    ):
        resource.total = (
            1000
            if bakery
            else (
                {"S": 12, "M": 18, "L": 30}
                if resource.details["variant"] == "navy"
                else {"S": 12, "M": 30, "L": 30}
            )[resource.details["size"]]
        )
    tz = ZoneInfo(workspace.timezone)
    requested_day = date(2026, 9, 18 if bakery else 17)
    if relative_dates:
        earliest = utcnow().astimezone(tz).date() + timedelta(days=4)
        weekday = 4 if bakery else 3  # Friday for bakery; Thursday for merchandise.
        requested_day = earliest + timedelta(days=(weekday - earliest.weekday()) % 7)
    original_day = requested_day + timedelta(days=1)
    dates = {
        requested_day.isoformat(): 60 if bakery else 32,
        original_day.isoformat(): 100 if bakery else 60,
    }
    for day, quantity in dates.items():
        resource = _resource(
            db,
            workspace,
            f"capacity:{day}",
            "capacity",
            f"Production · {day}",
            "prep minutes" if bakery else "shirts",
            quantity,
            {"date": day},
        )
        resource.total = quantity
    pickup = datetime.combine(original_day, time(10 if bakery else 16), tzinfo=tz)
    values = {
        "customer_name": "Maya Chen" if bakery else "Field Notes Club",
        "customer_email": None,
        "product_id": product.id,
        "quantity": 24 if bakery else 30,
        "variant": "vanilla" if bakery else "navy",
        "sizes": {} if bakery else {"S": 6, "M": 12, "L": 12},
        "pickup_at": pickup.isoformat(),
    }
    order = create_order(
        db, workspace.id, values, is_demo=True, number="BK-2082" if bakery else "OT-1048"
    )
    revision = db.scalar(select(OrderRevision).where(OrderRevision.order_id == order.id))
    issues, resources = availability(db, order, revision.resource_requirements)
    if issues:
        raise ValueError(f"Synthetic fixture resource mismatch: {issues}")
    revision.status = "accepted"
    order.accepted_revision_id = revision.id
    order.needs_attention = False
    for key, quantity in revision.resource_requirements.items():
        db.add(
            Reservation(
                workspace_id=workspace.id,
                order_id=order.id,
                revision_id=revision.id,
                resource_id=resources[key].id,
                quantity=quantity,
            )
        )
    db.add(
        Deposit(
            workspace_id=workspace.id,
            order_id=order.id,
            amount_cents=4800 if bakery else 27000,
            reference="SAMPLE initial deposit — no real payment",
            idempotency_key="sample-initial-deposit",
        )
    )
    event(
        db,
        order,
        "sample_acceptance",
        "Synthetic fixture: original revision accepted and sample deposit recorded. This is not a real customer agreement or payment.",
    )
    requested_label = (
        requested_day.strftime("%A %Y-%m-%d")
        if relative_dates
        else ("Friday" if bakery else "Thursday")
    )
    body = (
        f"Could we make it 36 cupcakes, with the same vanilla flavor and blue icing, and collect {requested_label} at 3 pm instead? Please send the new price before I confirm."
        if bakery
        else f"Could we add 15 medium shirts and collect on {requested_label} at noon instead? Navy if possible. Please tell me the price difference before I confirm."
    )
    record_message(db, workspace.id, order.id, body, source="sample")
    requested_pickup = datetime.combine(requested_day, time(15 if bakery else 12), tzinfo=tz)
    analyze_change(
        db,
        workspace.id,
        order.id,
        {
            "intent": "change_request",
            "quantity": 36 if bakery else 45,
            "variant": "vanilla" if bakery else "navy",
            "sizes": {} if bakery else {"S": 6, "M": 27, "L": 12},
            "pickup_at": requested_pickup.isoformat(),
            "missing_fields": [],
            "evidence": [{"quote": body, "source": "Synthetic demo fixture"}],
        },
    )
