"""Handover writes must serialize on PostgreSQL, not just pass SQLite tests.

Set OTW_TEST_DATABASE_URL to a disposable PostgreSQL database. Every test uses
and removes only its own uniquely named schema; no model or network service is used.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from ordertowork.db import Base, utcnow
from ordertowork.models.core import User, Workspace
from ordertowork.models.domain import Deposit, Order, OrderEvent, Reservation
from ordertowork.models.handover import Handover
from ordertowork.services import handover, orders
from ordertowork.services.profiles import seed_workspace
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_handover():
    database_url = os.environ.get("OTW_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("OTW_TEST_DATABASE_URL is not configured")
    assert database_url.startswith("postgresql"), "PostgreSQL is required for concurrency tests"
    schema = f"otw_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as db:
            owner = User(
                provider_subject="test:handover-owner", email="owner@example.test", name="Owner"
            )
            workspace = Workspace(name="Handover race fixture", profile="merchandise", is_demo=True)
            db.add_all([owner, workspace])
            db.flush()
            seed_workspace(db, workspace, relative_dates=True)
            order = db.scalar(select(Order).where(Order.workspace_id == workspace.id))
            ticket = orders.production_ticket(db, workspace.id, order.id)
            orders.start_production(db, workspace.id, order.id, ticket["revision"])
            window_start = (utcnow() + timedelta(days=2)).replace(microsecond=0)
            prepared = handover.ready(
                db,
                workspace.id,
                order.id,
                {
                    "expected_revision": ticket["revision"],
                    "collection_address": "12 Sample Street, Example City",
                    "collection_instructions": "Collection desk; bring the order number.",
                    "collection_window_start": window_start,
                    "collection_window_end": window_start + timedelta(days=2),
                    "delivery_mode": "included",
                    "delivery_fee_cents": 0,
                    "delivery_area": "Example City; business checks the address",
                },
            )["handover"]
            shared = handover.share(db, workspace.id, order.id)
            case = SimpleNamespace(
                engine=engine,
                workspace_id=workspace.id,
                order_id=order.id,
                user_id=owner.id,
                handover_id=prepared["id"],
                token=shared["url"].rsplit("/", 1)[1],
                collection_at=window_start + timedelta(hours=1),
            )
            db.commit()
        yield case
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def collection(case, version):
    return {
        "expected_version": version,
        "method": "collection",
        "collection_at": case.collection_at,
        "delivery_address": "",
        "contact_phone": "",
        "customer_note": "Sample collection",
        "consent": True,
    }


def recorded_receipt(amount, key):
    return {
        "amount_cents": amount,
        "reference": "SAMPLE receipt; no real payment",
        "idempotency_key": key,
    }


def event_count(db, case):
    return db.scalar(
        select(func.count()).select_from(OrderEvent).where(OrderEvent.order_id == case.order_id)
    )


def test_customer_choice_cannot_race_a_dispatch_of_different_arrangements(postgres_handover):
    case = postgres_handover
    with Session(case.engine) as db:
        ready = handover.customer_view(db, case.token)["handover"]
        requested = handover.choose(
            db,
            case.token,
            {
                "expected_version": ready["version"],
                "method": "delivery",
                "collection_at": None,
                "delivery_address": "24 Sample Lane, Example City",
                "contact_phone": "+1 555 010 0300",
                "customer_note": "Sample only",
                "consent": True,
            },
        )["handover"]
        quoted = handover.quote(
            db,
            case.workspace_id,
            case.order_id,
            {
                "expected_version": requested["version"],
                "delivery_fee_cents": 0,
                "note": "Included delivery; address checked.",
            },
        )["handover"]
        confirmed = handover.accept_quote(
            db,
            case.token,
            {
                "expected_version": quoted["version"],
                "quote_hash": quoted["quote_hash"],
                "consent": True,
            },
        )["handover"]
        paid = handover.record_payment(
            db,
            case.workspace_id,
            case.order_id,
            recorded_receipt(confirmed["pricing"]["balance_cents"], "sample-paid-before-race"),
            case.user_id,
        )["handover"]
        before_events = event_count(db, case)
        db.commit()
    barrier = Barrier(2)

    def competing_write(action):
        with Session(case.engine) as db:
            barrier.wait(timeout=10)
            try:
                if action == "dispatch":
                    result = handover.transition(
                        db, case.workspace_id, case.order_id, action, paid["version"]
                    )
                else:
                    result = handover.choose(db, case.token, collection(case, paid["version"]))
                db.commit()
                return action, "ok", result["handover"]["status"]
            except HTTPException as exc:
                db.rollback()
                return action, exc.detail["code"], None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(competing_write, ("dispatch", "collection")))
    assert sorted(outcome for _, outcome, _ in results) == ["handover_changed", "ok"]
    winner = next(action for action, outcome, _ in results if outcome == "ok")
    with Session(case.engine) as db:
        final = db.get(Handover, case.handover_id)
        assert final.version == paid["version"] + 1
        if winner == "dispatch":
            assert final.status == "out_for_delivery" and final.method == "delivery"
            assert final.dispatched_at is not None and final.collection_at is None
        else:
            assert final.status == "confirmed" and final.method == "collection"
            assert final.dispatched_at is None and final.delivery_address is None
        assert event_count(db, case) == before_events + 1
        assert all(
            reservation.status == "consumed"
            for reservation in db.scalars(
                select(Reservation).where(Reservation.order_id == case.order_id)
            )
        )


@pytest.mark.parametrize("same_key", [True, False])
def test_simultaneous_receipts_cannot_double_record_or_overpay(postgres_handover, same_key):
    case = postgres_handover
    with Session(case.engine) as db:
        ready = handover.customer_view(db, case.token)["handover"]
        confirmed = handover.choose(db, case.token, collection(case, ready["version"]))["handover"]
        before_events = event_count(db, case)
        db.commit()
    barrier = Barrier(2)
    first_key = "sample-simultaneous-receipt"
    second_key = first_key if same_key else "sample-another-full-receipt"

    def record(key):
        with Session(case.engine) as db:
            barrier.wait(timeout=10)
            try:
                result = handover.record_payment(
                    db,
                    case.workspace_id,
                    case.order_id,
                    recorded_receipt(confirmed["pricing"]["balance_cents"], key),
                    case.user_id,
                )
                db.commit()
                return "ok", result["handover"]["pricing"]
            except HTTPException as exc:
                db.rollback()
                return exc.detail["code"], None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(record, (first_key, second_key)))
    expected = ["ok", "ok"] if same_key else ["ok", "payment_exceeds_balance"]
    assert sorted(outcome for outcome, _ in results) == expected
    if same_key:
        assert results[0][1] == results[1][1]
    with Session(case.engine) as db:
        receipts = list(
            db.scalars(
                select(Deposit).where(
                    Deposit.order_id == case.order_id,
                    Deposit.idempotency_key.in_((first_key, second_key)),
                )
            )
        )
        assert len(receipts) == 1
        final = handover.owner_view(db, case.workspace_id, case.order_id)["handover"]
        assert final["pricing"]["balance_cents"] == 0
        assert final["pricing"]["paid_cents"] == final["pricing"]["total_cents"]
        assert final["version"] == confirmed["version"] + 1
        assert event_count(db, case) == before_events + 1
