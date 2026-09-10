"""Business invariant tests. SQLite covers deterministic behavior, not row locks."""

from datetime import timedelta

import pytest
from fastapi import HTTPException
from ordertowork.db import Base, utcnow
from ordertowork.models import jobs  # noqa: F401 -- register tables for snapshot queries
from ordertowork.models.core import Workspace
from ordertowork.models.domain import (
    ApprovalLink,
    Deposit,
    Order,
    OrderRevision,
    Product,
    Reservation,
    Resource,
)
from ordertowork.services import orders as service
from ordertowork.services.profiles import configure_workspace, seed_workspace
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session


@pytest.fixture
def domain_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def demo(db, profile="merchandise"):
    workspace = Workspace(name="Demo business", profile=profile, is_demo=True)
    db.add(workspace)
    db.flush()
    seed_workspace(db, workspace)
    db.commit()
    order = db.scalar(select(Order).where(Order.workspace_id == workspace.id))
    return workspace, order


def options(db, order):
    return list(
        db.scalars(
            select(OrderRevision)
            .where(
                OrderRevision.order_id == order.id,
                OrderRevision.status == "proposed",
                OrderRevision.feasible.is_(True),
            )
            .order_by(OrderRevision.number)
        )
    )


def share(db, workspace, order, revision):
    shared = service.share_proposal(db, workspace.id, order.id, revision.id)
    db.commit()
    return shared["url"].rsplit("/", 1)[1]


def active_holds(db, order):
    return {
        (r.resource_id, r.quantity, r.revision_id, r.status)
        for r in db.scalars(
            select(Reservation).where(
                Reservation.order_id == order.id, Reservation.status.in_(("held", "consumed"))
            )
        )
    }


def expect_error(code, fn):
    with pytest.raises(HTTPException) as error:
        fn()
    assert error.value.detail["code"] == code


def test_canonical_fixtures_and_exploration_do_not_change_commitments(domain_db):
    for profile, amount, expected in (
        ("merchandise", 54000, [81000, 63000]),
        ("bakery", 9600, [14400, 11600]),
    ):
        workspace, order = demo(domain_db, profile)
        original = domain_db.get(OrderRevision, order.accepted_revision_id)
        assert original.number == 1
        assert original.terms["total_cents"] == amount
        assert [r.terms["total_cents"] for r in options(domain_db, order)] == expected
        assert all(row[2] == original.id for row in active_holds(domain_db, order))
        assert service.paid_cents(domain_db, order) == amount // 2
        detail = service.order_detail(domain_db, workspace.id, order.id)
        assert detail["latest_analysis"]["requested_issues"]
        assert detail["latest_job"] is None
        assert detail["is_demo"]


def test_exact_approval_swaps_holds_once_and_payment_gates_ticket(domain_db):
    workspace, order = demo(domain_db)
    original_id = order.accepted_revision_id
    revision = options(domain_db, order)[0]
    token = share(domain_db, workspace, order, revision)
    before = active_holds(domain_db, order)
    expect_error(
        "consent_required",
        lambda: service.approve_customer(domain_db, token, revision.terms_hash, False),
    )
    expect_error(
        "terms_mismatch", lambda: service.approve_customer(domain_db, token, "0" * 64, True)
    )
    assert before == active_holds(domain_db, order)
    assert (
        service.approve_customer(domain_db, token, revision.terms_hash, True)["status"]
        == "approved"
    )
    domain_db.commit()
    assert order.accepted_revision_id == revision.id != original_id
    swapped = active_holds(domain_db, order)
    assert all(row[2] == revision.id for row in swapped)
    assert (
        service.approve_customer(domain_db, token, revision.terms_hash, True)["status"]
        == "already_approved"
    )
    domain_db.commit()
    assert active_holds(domain_db, order) == swapped
    expect_error(
        "production_blocked", lambda: service.production_ticket(domain_db, workspace.id, order.id)
    )
    service.record_deposit(
        domain_db, workspace.id, order.id, 13500, "Bank transfer123", "deposit-123", None
    )
    domain_db.commit()
    ticket = service.production_ticket(domain_db, workspace.id, order.id)
    assert ticket["revision"] == revision.number
    assert ticket["balance_cents"] == 40500
    assert ticket["terms"]["sizes"] == {"S": 6, "M": 27, "L": 12}
    assert ticket["is_demo"]
    receipt = service.customer_view(domain_db, token)
    assert receipt["status"] == "approved"
    assert receipt["previous_terms"]["total_cents"] == 54000
    assert "customer_email" not in receipt["order"]
    assert "events" not in receipt
    assert receipt["revision"]["issues"] == []


def test_approval_availability_failure_preserves_original_and_returns_owner_action(domain_db):
    workspace, order = demo(domain_db)
    revision = options(domain_db, order)[0]
    token = share(domain_db, workspace, order, revision)
    original_id = order.accepted_revision_id
    before = active_holds(domain_db, order)
    resource = domain_db.scalar(
        select(Resource).where(
            Resource.workspace_id == workspace.id, Resource.key.contains(":charcoal:M")
        )
    )
    service.update_resource(domain_db, workspace.id, resource.id, 20)
    domain_db.commit()
    expect_error(
        "availability_changed",
        lambda: service.approve_customer(domain_db, token, revision.terms_hash, True),
    )
    domain_db.commit()  # Public API deliberately persists diagnostic updates only.
    assert order.accepted_revision_id == original_id
    assert active_holds(domain_db, order) == before
    assert service.order_detail(domain_db, workspace.id, order.id)["status"] == "needs_review"
    expect_error("link_unavailable", lambda: service.customer_view(domain_db, token))


def test_replacement_share_and_expiry_invalidate_old_links(domain_db):
    workspace, order = demo(domain_db)
    first, second = options(domain_db, order)
    token1 = share(domain_db, workspace, order, first)
    token2 = share(domain_db, workspace, order, second)
    expect_error(
        "link_unavailable",
        lambda: service.approve_customer(domain_db, token1, first.terms_hash, True),
    )
    link = domain_db.scalar(select(ApprovalLink).where(ApprovalLink.revision_id == second.id))
    link.expires_at = utcnow() - timedelta(seconds=1)
    domain_db.commit()
    expect_error("link_unavailable", lambda: service.customer_view(domain_db, token2))
    assert domain_db.get(OrderRevision, order.accepted_revision_id).number == 1


def test_customer_change_request_keeps_current_agreement_and_resources(domain_db):
    workspace, order = demo(domain_db)
    revision = options(domain_db, order)[0]
    token = share(domain_db, workspace, order, revision)
    original_id = order.accepted_revision_id
    before = active_holds(domain_db, order)
    service.request_customer_change(domain_db, token, "Can we keep navy and choose next week?")
    domain_db.commit()
    assert order.accepted_revision_id == original_id
    assert active_holds(domain_db, order) == before
    detail = service.order_detail(domain_db, workspace.id, order.id)
    assert detail["status"] == "needs_review"
    assert detail["messages"][-1]["source"] == "customer_link"
    expect_error(
        "link_unavailable",
        lambda: service.approve_customer(domain_db, token, revision.terms_hash, True),
    )


def test_payment_idempotency_conflict_and_explicit_hold(domain_db):
    workspace, order = demo(domain_db)
    service.record_deposit(
        domain_db, workspace.id, order.id, 100, "manual receipt", "same-key", None
    )
    service.record_deposit(
        domain_db, workspace.id, order.id, 100, "manual receipt", "same-key", None
    )
    domain_db.commit()
    assert service.paid_cents(domain_db, order) == 27100
    assert (
        domain_db.scalar(
            select(func.count()).select_from(Deposit).where(Deposit.order_id == order.id)
        )
        == 2
    )
    expect_error(
        "idempotency_conflict",
        lambda: service.record_deposit(
            domain_db, workspace.id, order.id, 200, "manual receipt", "same-key", None
        ),
    )
    service.set_hold(domain_db, workspace.id, order.id, "Awaiting artwork review")
    expect_error(
        "production_blocked", lambda: service.production_ticket(domain_db, workspace.id, order.id)
    )
    service.set_hold(domain_db, workspace.id, order.id, None)
    assert service.production_ticket(domain_db, workspace.id, order.id)["revision"] == 1


def test_started_production_consumes_holds_and_refuses_automatic_changes(domain_db):
    workspace, order = demo(domain_db)
    revision = options(domain_db, order)[0]
    token = share(domain_db, workspace, order, revision)
    service.start_production(domain_db, workspace.id, order.id)
    domain_db.commit()
    assert order.production_status == "started"
    assert all(row[3] == "consumed" for row in active_holds(domain_db, order))
    expect_error(
        "link_unavailable",
        lambda: service.approve_customer(domain_db, token, revision.terms_hash, True),
    )
    expect_error(
        "production_started",
        lambda: service.create_proposal(domain_db, workspace.id, order.id, revision.terms),
    )
    before = len(service.revisions_for(domain_db, order))
    service.analyze_change(
        domain_db, workspace.id, order.id, {**revision.terms, "intent": "change_request"}
    )
    assert len(service.revisions_for(domain_db, order)) == before
    assert order.accepted_revision_id != revision.id


def test_model_approval_questions_and_missing_details_never_authorize(domain_db):
    workspace, order = demo(domain_db)
    original_id = order.accepted_revision_id
    holds = active_holds(domain_db, order)
    count = len(service.revisions_for(domain_db, order))
    for request in (
        {"intent": "approval"},
        {"intent": "question"},
        {"intent": "change_request", "missing_fields": ["size counts"]},
    ):
        service.analyze_change(domain_db, workspace.id, order.id, request)
        assert order.accepted_revision_id == original_id
        assert len(service.revisions_for(domain_db, order)) == count
        assert active_holds(domain_db, order) == holds


def test_preview_is_read_only_and_settings_do_not_change_quoted_agreement(domain_db):
    workspace, order = demo(domain_db)
    revision = options(domain_db, order)[0]
    token = share(domain_db, workspace, order, revision)
    count = len(service.revisions_for(domain_db, order))
    preview = service.preview_change(domain_db, workspace.id, order.id, revision.terms)
    assert preview["feasible"]
    assert len(service.revisions_for(domain_db, order)) == count
    product = domain_db.get(Product, revision.terms["product_id"])
    product.unit_price_cents = 9999
    product.capacity_units_per_item = 10
    workspace.deposit_bps = 10000
    workspace.timezone = "Asia/Kolkata"
    domain_db.commit()
    service.approve_customer(domain_db, token, revision.terms_hash, True)
    domain_db.commit()
    assert revision.terms["total_cents"] == 81000
    assert revision.terms["required_deposit_cents"] == 40500
    assert revision.terms["timezone"] == "America/New_York"
    assert sum(row[1] for row in active_holds(domain_db, order)) == 90


def test_tenant_scope_and_committed_resource_floor(domain_db):
    workspace, order = demo(domain_db)
    other, _ = demo(domain_db, "bakery")
    expect_error("not_found", lambda: service.order_detail(domain_db, other.id, order.id))
    resource = domain_db.scalar(
        select(Resource).where(
            Resource.workspace_id == workspace.id, Resource.key.contains(":navy:M")
        )
    )
    expect_error(
        "not_found", lambda: service.update_resource(domain_db, other.id, resource.id, 100)
    )
    expect_error(
        "resource_committed",
        lambda: service.update_resource(domain_db, workspace.id, resource.id, 11),
    )
    assert resource.total == 18


def test_real_workspace_does_not_invent_stock_approval_or_payment(domain_db):
    workspace = Workspace(name="Actual business", profile="bakery", is_demo=False)
    domain_db.add(workspace)
    domain_db.flush()
    product = configure_workspace(domain_db, workspace)
    assert not domain_db.scalar(select(Order.id).where(Order.workspace_id == workspace.id))
    assert all(
        r.total == 0
        for r in domain_db.scalars(select(Resource).where(Resource.workspace_id == workspace.id))
    )
    order = service.create_order(
        domain_db,
        workspace.id,
        {
            "customer_name": "New customer",
            "product_id": product.id,
            "quantity": 10,
            "variant": "vanilla",
            "pickup_at": (utcnow() + timedelta(days=4)).isoformat(),
        },
    )
    domain_db.commit()
    assert order.accepted_revision_id is None
    assert not order.is_demo
    assert not active_holds(domain_db, order)
    assert service.paid_cents(domain_db, order) == 0
    expect_error(
        "production_blocked", lambda: service.production_ticket(domain_db, workspace.id, order.id)
    )


def test_bakery_new_order_can_be_accepted_after_owner_configures_resources(domain_db):
    workspace = Workspace(name="Real bakery", profile="bakery")
    domain_db.add(workspace)
    domain_db.flush()
    product = configure_workspace(domain_db, workspace)
    for resource in domain_db.scalars(
        select(Resource).where(Resource.workspace_id == workspace.id)
    ):
        resource.total = 100
    order = service.create_order(
        domain_db,
        workspace.id,
        {
            "customer_name": "A customer",
            "product_id": product.id,
            "quantity": 10,
            "variant": "vanilla",
            "pickup_at": (utcnow() + timedelta(days=4)).isoformat(),
        },
    )
    revision = service.revisions_for(domain_db, order)[0]
    token = share(domain_db, workspace, order, revision)
    service.approve_customer(domain_db, token, revision.terms_hash, True)
    service.record_deposit(
        domain_db,
        workspace.id,
        order.id,
        revision.terms["required_deposit_cents"],
        "received externally",
        "external-1",
        None,
    )
    domain_db.commit()
    ticket = service.production_ticket(domain_db, workspace.id, order.id)
    assert ticket["revision"] == 1
    assert not ticket["is_demo"]
    assert ticket["terms"]["quantity"] == 10


def test_invalid_size_count_and_naive_time_are_rejected(domain_db):
    workspace, order = demo(domain_db)
    terms = options(domain_db, order)[0].terms
    expect_error(
        "size_total_mismatch",
        lambda: service.create_proposal(
            domain_db, workspace.id, order.id, {**terms, "quantity": 46}
        ),
    )
    expect_error(
        "invalid_sizes",
        lambda: service.create_proposal(
            domain_db, workspace.id, order.id, {**terms, "sizes": {"M": True}}
        ),
    )
    expect_error(
        "pickup_timezone_required",
        lambda: service.create_proposal(
            domain_db, workspace.id, order.id, {**terms, "pickup_at": "2026-09-18T16:00:00"}
        ),
    )


def test_shared_terms_cannot_change_without_new_exact_approval(domain_db):
    workspace, order = demo(domain_db)
    revision = options(domain_db, order)[0]
    token = share(domain_db, workspace, order, revision)
    revision.terms = {**revision.terms, "quantity": 999}
    domain_db.commit()
    expect_error(
        "terms_mismatch",
        lambda: service.approve_customer(domain_db, token, revision.terms_hash, True),
    )
    assert domain_db.get(OrderRevision, order.accepted_revision_id).number == 1


def test_proposal_base_must_still_match_current_accepted_revision(domain_db):
    workspace, order = demo(domain_db)
    revision = options(domain_db, order)[0]
    token = share(domain_db, workspace, order, revision)
    # An out-of-band owner-resolution path changed canonical agreement.
    order.accepted_revision_id = options(domain_db, order)[-1].id
    domain_db.commit()
    expect_error(
        "stale_revision",
        lambda: service.approve_customer(domain_db, token, revision.terms_hash, True),
    )


def test_operator_ticket_and_queue_exclude_private_commercial_data(domain_db):
    workspace, order = demo(domain_db)
    ticket = service.operational_ticket(
        service.production_ticket(domain_db, workspace.id, order.id)
    )
    assert "deposit_paid_cents" not in ticket
    assert "balance_cents" not in ticket
    assert "total_cents" not in ticket["terms"]
    assert "required_deposit_cents" not in ticket["terms"]
    assert "currency" not in ticket["terms"]
    queue = service.production_queue(domain_db, workspace.id)
    assert len(queue["orders"]) == 1
    assert "messages" not in queue["orders"][0]
    assert "customer_email" not in queue["orders"][0]
