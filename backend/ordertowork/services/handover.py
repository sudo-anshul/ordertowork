"""Ready, customer choice, exact delivery consent and recorded handover.

The accepted production revision never changes here. Every write locks the order
first, so customer choices, quotes, payment receipts and dispatch cannot race.
No operation sends a message, collects money or invokes a model.
"""

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta

from ordertowork.config import get_settings
from ordertowork.db import utcnow
from ordertowork.models.domain import Deposit, Order, OrderRevision
from ordertowork.models.handover import Handover, HandoverLink
from ordertowork.services import orders
from sqlalchemy import select
from sqlalchemy.orm import Session

FINAL_STATES = {"collected", "delivered"}
CHOICE_STATES = {"awaiting_choice", "delivery_requested", "quote_ready", "confirmed"}
DEFAULT_KEYS = (
    "collection_address",
    "collection_instructions",
    "delivery_mode",
    "delivery_fee_cents",
    "delivery_area",
)


def for_order(db: Session, order: Order) -> Handover | None:
    return db.scalar(
        select(Handover)
        .where(Handover.order_id == order.id, Handover.workspace_id == order.workspace_id)
        .execution_options(populate_existing=True)
    )


def defaults(workspace) -> dict:
    if workspace.handover_settings:
        return dict(workspace.handover_settings)
    sample = workspace.is_demo
    return {
        "collection_address": "12 Sample Lane (fictional address)" if sample else "",
        "collection_instructions": "Please arrange a time within the collection window."
        if sample
        else "",
        "delivery_mode": "fixed" if sample else "unavailable",
        "delivery_fee_cents": 500 if sample else 0,
        "delivery_area": "Local area only; we confirm your address before quoting delivery."
        if sample
        else "",
    }


def pricing(db: Session, order: Order, handover: Handover) -> dict:
    revision = db.get(OrderRevision, handover.revision_id)
    subtotal = revision.terms["total_cents"]
    fee = handover.delivery_fee_cents
    total = subtotal + fee if fee is not None else None
    paid = orders.paid_cents(db, order)
    return {
        "order_total_cents": subtotal,
        "delivery_fee_cents": fee,
        "total_cents": total,
        "paid_cents": paid,
        "balance_cents": max(0, total - paid) if total is not None else None,
        "currency": revision.terms["currency"],
    }


def payload(db: Session, order: Order, handover: Handover) -> dict:
    revision = db.get(OrderRevision, handover.revision_id)
    active_link = db.scalar(
        select(HandoverLink)
        .where(
            HandoverLink.handover_id == handover.id,
            HandoverLink.status == "active",
            HandoverLink.expires_at > utcnow(),
        )
        .order_by(HandoverLink.created_at.desc())
        .limit(1)
    )
    return {
        "id": handover.id,
        "version": handover.version,
        "status": handover.status,
        "revision": revision.number,
        "config": handover.config,
        "method": handover.method,
        "collection_at": orders.iso(handover.collection_at) if handover.collection_at else None,
        "delivery_address": handover.delivery_address,
        "contact_phone": handover.contact_phone,
        "customer_note": handover.customer_note,
        "delivery_fee_cents": handover.delivery_fee_cents,
        "quote_note": handover.quote_note,
        "quote_hash": handover.quote_hash,
        "pricing": pricing(db, order, handover),
        "on_hold": bool(order.hold_reason),
        **{
            name: orders.iso(value) if (value := getattr(handover, name)) else None
            for name in ("ready_at", "confirmed_at", "dispatched_at", "completed_at")
        },
        "link_active": bool(active_link),
        "link_expires_at": orders.iso(active_link.expires_at) if active_link else None,
    }


def owner_view(db: Session, workspace_id: str, order_id: str) -> dict:
    workspace = orders.get_workspace(db, workspace_id)
    order = orders.get_order(db, workspace_id, order_id)
    handover = for_order(db, order)
    return {
        "handover": payload(db, order, handover) if handover else None,
        "defaults": defaults(workspace),
    }


def context(db: Session, workspace_id: str, order_id: str) -> tuple[Order, Handover]:
    order = orders.get_order(db, workspace_id, order_id, lock=True)
    handover = for_order(db, order)
    if not handover:
        orders.fail("handover_not_ready", "Prepare this order for handover first.")
    if order.accepted_revision_id != handover.revision_id or order.production_status != "finished":
        orders.fail(
            "handover_revision_changed", "The prepared handover no longer matches the order."
        )
    return order, handover


def no_hold(order: Order) -> None:
    if order.hold_reason:
        orders.fail(
            "handover_on_hold", "The business has paused this handover. Contact them to continue."
        )


def check_version(handover: Handover, version: int) -> None:
    if isinstance(version, bool) or version != handover.version:
        orders.fail(
            "handover_changed", "Handover details changed. Reload and review the current details."
        )


def validate_window(start: datetime, end: datetime) -> None:
    now = utcnow()
    if (
        end <= start
        or end <= now
        or start > now + timedelta(days=30)
        or end > now + timedelta(days=30)
        or start < now - timedelta(days=1)
    ):
        orders.fail(
            "invalid_collection_window",
            "Choose an upcoming collection window ending within 30 days, starting no more than a day ago.",
            422,
        )


def ready(db: Session, workspace_id: str, order_id: str, values: dict) -> dict:
    order = orders.get_order(db, workspace_id, order_id, lock=True)
    workspace = orders.get_workspace(db, workspace_id)
    revision = (
        db.get(OrderRevision, order.accepted_revision_id) if order.accepted_revision_id else None
    )
    if not revision or revision.number != values["expected_revision"]:
        orders.fail("stale_revision", "Reload and review the current accepted revision.")
    config = {key: values[key] for key in DEFAULT_KEYS}
    config.update(
        timezone=workspace.timezone,
        collection_window_start=orders.iso(values["collection_window_start"]),
        collection_window_end=orders.iso(values["collection_window_end"]),
    )
    existing = for_order(db, order)
    if existing:
        if existing.config == config and existing.revision_id == revision.id:
            return owner_view(db, workspace_id, order_id)
        orders.fail(
            "handover_already_ready",
            "This order already has handover terms. Its customer choices have been preserved.",
        )
    start, end = values["collection_window_start"], values["collection_window_end"]
    validate_window(start, end)
    if order.production_status not in {"started", "finished"}:
        orders.fail(
            "production_not_started",
            "Start and finish the accepted production work before preparing handover.",
        )
    no_hold(order)
    if order.production_status == "started":
        orders.finish_production(db, workspace_id, order_id, revision.number)
    handover = Handover(
        workspace_id=workspace_id, order_id=order.id, revision_id=revision.id, config=config
    )
    db.add(handover)
    workspace.handover_settings = {key: config[key] for key in DEFAULT_KEYS}
    order.needs_attention = False
    orders.event(
        db,
        order,
        "handover_ready",
        f"Revision {revision.number} is ready for collection or delivery arrangements. No notification has been sent.",
    )
    db.flush()
    return owner_view(db, workspace_id, order_id)


def share(db: Session, workspace_id: str, order_id: str) -> dict:
    order, handover = context(db, workspace_id, order_id)
    workspace = orders.get_workspace(db, workspace_id)
    for link in db.scalars(
        select(HandoverLink).where(
            HandoverLink.handover_id == handover.id, HandoverLink.status == "active"
        )
    ):
        link.status = "revoked"
    token = secrets.token_urlsafe(32)
    expires = utcnow() + timedelta(days=14)
    if workspace.demo_expires_at:
        expires = min(expires, orders.aware(workspace.demo_expires_at))
    db.add(
        HandoverLink(
            handover_id=handover.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=expires,
        )
    )
    url = f"{get_settings().app_url.rstrip('/')}/handover/{token}"
    stage = {
        "awaiting_choice": "Your order is ready. Choose collection or request delivery using your private link.",
        "delivery_requested": "We have your delivery request. We will check the address and prepare the delivery terms for your approval.",
        "quote_ready": "Your delivery terms are ready. Review the address, delivery fee and remaining balance, then confirm using your private link.",
        "confirmed": "Your handover arrangements are confirmed. View your collection or delivery details and remaining balance using your private link.",
        "out_for_delivery": "Your order is out for delivery. View the latest handover status using your private link.",
        "collected": "Your order has been collected. Thank you! You can view the completed handover using your private link.",
        "delivered": "Your order has been delivered. Thank you! You can view the completed handover using your private link.",
    }[handover.status]
    if order.hold_reason:
        stage = "Your handover is currently paused. Please contact us to arrange the next step; your private link shows the current status."
    sample = "SAMPLE ORDER — fictional demonstration.\n" if order.is_demo else ""
    text = f"{sample}Hi {order.customer_name},\n\n{stage}\n\nOrder {order.number} · {workspace.name}\n{url}\n\nThis page does not collect payment. Please arrange any balance directly with us."
    orders.event(
        db,
        order,
        "handover_link_created",
        "Private handover link and notification text prepared. No message was sent; any earlier handover link is replaced.",
    )
    db.flush()
    return {"url": url, "expires_at": orders.iso(expires), "notification_text": text}


def public_context(db: Session, token: str, *, lock=False):
    if not 40 <= len(token) <= 100:
        orders.fail(
            "handover_link_unavailable",
            "This handover link is unavailable or expired. Ask the business for a new link.",
            410,
        )
    link = db.scalar(
        select(HandoverLink).where(
            HandoverLink.token_hash == hashlib.sha256(token.encode()).hexdigest()
        )
    )
    if not link:
        orders.fail(
            "handover_link_unavailable",
            "This handover link is unavailable or expired. Ask the business for a new link.",
            410,
        )
    handover = db.get(Handover, link.handover_id)
    order = orders.get_order(db, handover.workspace_id, handover.order_id, lock=lock)
    # Serialize on the order, then refresh the link and choice after a concurrent write.
    if lock:
        db.refresh(link)
        db.refresh(handover)
    workspace = orders.get_workspace(db, order.workspace_id)
    if link.status != "active" or orders.aware(link.expires_at) <= utcnow():
        orders.fail(
            "handover_link_unavailable",
            "This handover link is unavailable or expired. Ask the business for a new link.",
            410,
        )
    if order.accepted_revision_id != handover.revision_id or order.production_status != "finished":
        orders.fail(
            "handover_link_unavailable",
            "This handover no longer matches the current order. Contact the business.",
            410,
        )
    return link, order, handover, workspace


def customer_view(db: Session, token: str) -> dict:
    link, order, handover, workspace = public_context(db, token)
    revision = db.get(OrderRevision, handover.revision_id)
    return {
        "business": {
            "name": workspace.name,
            "currency": revision.terms["currency"],
            "timezone": handover.config.get("timezone", revision.terms["timezone"]),
        },
        "order": {
            "number": order.number,
            "customer_name": order.customer_name,
            "is_demo": order.is_demo,
            "revision": revision.number,
            **{key: revision.terms[key] for key in ("product_name", "quantity", "variant")},
        },
        "handover": payload(db, order, handover),
        "expires_at": orders.iso(link.expires_at),
    }


def choose(db: Session, token: str, values: dict) -> dict:
    _, order, handover, _ = public_context(db, token, lock=True)
    check_version(handover, values["expected_version"])
    no_hold(order)
    if handover.status not in CHOICE_STATES:
        orders.fail(
            "handover_locked",
            "The order has already left the business. Contact them to change arrangements.",
        )
    if values["consent"] is not True:
        orders.fail(
            "consent_required", "Review and confirm your collection or delivery request.", 422
        )
    revision = db.get(OrderRevision, handover.revision_id)
    if orders.paid_cents(db, order) > revision.terms["total_cents"]:
        orders.fail(
            "payment_adjustment_required",
            "A delivery payment is already recorded. Contact the business to arrange a change or refund.",
        )
    method = values["method"]
    if method == "collection":
        when = values.get("collection_at")
        start = datetime.fromisoformat(handover.config["collection_window_start"])
        end = datetime.fromisoformat(handover.config["collection_window_end"])
        if when is None or when <= utcnow() or not start <= when <= end:
            orders.fail(
                "invalid_collection_time",
                "Choose a future time within the displayed collection window.",
                422,
            )
        handover.collection_at = when
        handover.delivery_address = None
        handover.contact_phone = None
        handover.delivery_fee_cents = 0
        handover.status = "confirmed"
        handover.confirmed_at = utcnow()
        message = (
            "Customer confirmed collection within the offered window; no delivery charge applies."
        )
    else:
        if handover.config["delivery_mode"] == "unavailable":
            orders.fail(
                "delivery_unavailable",
                "Delivery is not available for this order. Choose collection.",
                422,
            )
        address, phone = (
            values.get("delivery_address", "").strip(),
            values.get("contact_phone", "").strip(),
        )
        if len(address) < 5 or len(phone) < 5:
            orders.fail(
                "delivery_details_required",
                "Enter the complete delivery address and a contact phone number.",
                422,
            )
        handover.collection_at = None
        handover.delivery_address, handover.contact_phone = address, phone
        handover.delivery_fee_cents = None
        handover.status = "delivery_requested"
        handover.confirmed_at = None
        message = "Customer requested delivery. The business must check the address and prepare an exact quote before acceptance."
    handover.method = method
    handover.customer_note = values.get("customer_note", "").strip() or None
    handover.quote_hash = None
    handover.quote_note = None
    handover.version += 1
    orders.event(
        db,
        order,
        "handover_choice_received",
        message,
        {"method": method, "version": handover.version},
    )
    db.flush()
    return customer_view(db, token)


def quote_hash(handover: Handover, revision: OrderRevision) -> str:
    # Includes the exact address/fee/notes and version, never a client-supplied total.
    terms = {
        "handover_id": handover.id,
        "revision_hash": revision.terms_hash,
        "version": handover.version,
        "address": handover.delivery_address,
        "phone": handover.contact_phone,
        "fee_cents": handover.delivery_fee_cents,
        "currency": revision.terms["currency"],
        "note": handover.quote_note,
    }
    return hashlib.sha256(
        json.dumps(terms, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def update_window(db: Session, workspace_id: str, order_id: str, values: dict) -> dict:
    order, handover = context(db, workspace_id, order_id)
    check_version(handover, values["expected_version"])
    if handover.status not in CHOICE_STATES:
        orders.fail(
            "handover_locked", "Collection details cannot change after dispatch or completion."
        )
    validate_window(values["collection_window_start"], values["collection_window_end"])
    new_times = {
        name: orders.iso(values[name])
        for name in ("collection_window_start", "collection_window_end")
    }
    if all(handover.config[name] == value for name, value in new_times.items()):
        return owner_view(db, workspace_id, order_id)
    handover.config = {**handover.config, **new_times}
    handover.version += 1
    reconfirm = handover.method == "collection"
    if reconfirm:
        handover.status = "awaiting_choice"
        handover.method = None
        handover.collection_at = None
        handover.confirmed_at = None
        handover.delivery_fee_cents = None
        handover.quote_hash = None
        handover.quote_note = None
    elif handover.status == "quote_ready":
        handover.quote_hash = quote_hash(handover, db.get(OrderRevision, handover.revision_id))
    orders.event(
        db,
        order,
        "collection_window_updated",
        "The collection window changed. The customer must choose and confirm a new collection time."
        if reconfirm
        else "The collection window changed. Delivery terms and recorded payments are unchanged.",
        {"version": handover.version, "collection_reconfirmation_required": reconfirm},
    )
    db.flush()
    return owner_view(db, workspace_id, order_id)


def quote(db: Session, workspace_id: str, order_id: str, values: dict) -> dict:
    order, handover = context(db, workspace_id, order_id)
    check_version(handover, values["expected_version"])
    no_hold(order)
    if (
        handover.status not in {"delivery_requested", "quote_ready"}
        or handover.method != "delivery"
    ):
        orders.fail(
            "delivery_request_required",
            "A current customer delivery request is required before quoting.",
        )
    mode, fee = handover.config["delivery_mode"], values["delivery_fee_cents"]
    expected = 0 if mode == "included" else handover.config["delivery_fee_cents"]
    if mode == "unavailable" or (mode != "quote" and fee != expected):
        orders.fail(
            "delivery_fee_mismatch",
            "The delivery charge must match the configured fee. Included delivery has no additional charge.",
            422,
        )
    handover.delivery_fee_cents = fee
    handover.quote_note = values.get("note", "").strip() or None
    handover.status = "quote_ready"
    handover.version += 1
    handover.quote_hash = quote_hash(handover, db.get(OrderRevision, handover.revision_id))
    orders.event(
        db,
        order,
        "delivery_quote_prepared",
        "The business checked the delivery request and prepared an exact fee. Customer acceptance is still required.",
        {"fee_cents": fee, "version": handover.version},
    )
    db.flush()
    return owner_view(db, workspace_id, order_id)


def accept_quote(db: Session, token: str, values: dict) -> dict:
    _, order, handover, _ = public_context(db, token, lock=True)
    check_version(handover, values["expected_version"])
    no_hold(order)
    if values["consent"] is not True:
        orders.fail(
            "consent_required", "Explicitly accept the displayed delivery address and fee.", 422
        )
    if handover.status != "quote_ready" or handover.method != "delivery":
        orders.fail("delivery_quote_required", "A current delivery quote is required.")
    expected = quote_hash(handover, db.get(OrderRevision, handover.revision_id))
    if (
        not handover.quote_hash
        or not hmac.compare_digest(values["quote_hash"], handover.quote_hash)
        or not hmac.compare_digest(expected, handover.quote_hash)
    ):
        orders.fail(
            "quote_mismatch",
            "The delivery quote changed. Reload and review the current fee before accepting.",
        )
    handover.status = "confirmed"
    handover.confirmed_at = utcnow()
    handover.version += 1
    orders.event(
        db,
        order,
        "delivery_quote_accepted",
        "Customer explicitly accepted the delivery address and additional fee.",
        {"fee_cents": handover.delivery_fee_cents, "quote_hash": handover.quote_hash},
    )
    db.flush()
    return customer_view(db, token)


def record_payment(
    db: Session, workspace_id: str, order_id: str, values: dict, user_id: str
) -> dict:
    order, handover = context(db, workspace_id, order_id)
    existing = db.scalar(
        select(Deposit).where(
            Deposit.order_id == order.id, Deposit.idempotency_key == values["idempotency_key"]
        )
    )
    if existing:
        if (
            existing.amount_cents != values["amount_cents"]
            or existing.reference != values["reference"].strip()
        ):
            orders.fail(
                "idempotency_conflict", "This request key was used for a different payment receipt."
            )
        return owner_view(db, workspace_id, order_id)
    if handover.status != "confirmed":
        orders.fail(
            "handover_confirmation_required",
            "Confirm collection or the exact delivery quote before recording the final payment.",
        )
    balance = pricing(db, order, handover)["balance_cents"]
    if balance is None or values["amount_cents"] > balance:
        orders.fail(
            "payment_exceeds_balance",
            "The receipt exceeds the remaining balance. Reload the current amount.",
            422,
        )
    orders.record_deposit(
        db,
        workspace_id,
        order_id,
        values["amount_cents"],
        values["reference"],
        values["idempotency_key"],
        user_id,
        payment_kind="payment",
    )
    handover.version += 1
    db.flush()
    return owner_view(db, workspace_id, order_id)


def transition(db: Session, workspace_id: str, order_id: str, action: str, version: int) -> dict:
    order, handover = context(db, workspace_id, order_id)
    check_version(handover, version)
    no_hold(order)
    expected_status, method, target = {
        "collect": ("confirmed", "collection", "collected"),
        "dispatch": ("confirmed", "delivery", "out_for_delivery"),
        "deliver": ("out_for_delivery", "delivery", "delivered"),
    }[action]
    if handover.status == target and handover.method == method:
        return owner_view(db, workspace_id, order_id)
    if handover.status != expected_status or handover.method != method:
        orders.fail(
            "invalid_handover_transition",
            "The customer's confirmed choice does not allow this handover step. Reload the current status.",
        )
    if pricing(db, order, handover)["balance_cents"] != 0:
        orders.fail(
            "handover_balance_due",
            "Record the remaining payment received before collection or dispatch.",
        )
    handover.status = target
    handover.version += 1
    if action == "dispatch":
        handover.dispatched_at = utcnow()
    else:
        handover.completed_at = utcnow()
        order.needs_attention = False
    orders.event(
        db,
        order,
        "handover_" + target,
        {
            "collect": "Order collected by the customer. Handover completed.",
            "dispatch": "Order marked out for delivery by the business.",
            "deliver": "Order marked delivered by the business. Handover completed.",
        }[action],
        {"revision_id": handover.revision_id},
    )
    db.flush()
    return owner_view(db, workspace_id, order_id)
