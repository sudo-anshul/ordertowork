"""The customer handover contract, exercised through real authenticated HTTP routes.

All orders and recorded receipts here are synthetic. These tests never run a
worker, contact a model, send a notification, or process a real payment.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from ordertowork.config import get_settings
from ordertowork.db import session_factory, utcnow
from ordertowork.models.domain import Deposit, Order, Reservation, Resource, SourceMessage
from ordertowork.models.handover import HandoverLink
from ordertowork.models.jobs import AgentDailyUsage, Job
from ordertowork.services import handover as handover_service
from ordertowork.services.auth import digest
from ordertowork.services.jobs import claim_next
from sqlalchemy import func, select


def successful(response, expected=200):
    assert response.status_code == expected, response.text
    return response.json()


def login(client, email):
    session = successful(
        client.post("/api/auth/development-login", json={"email": email, "name": email})
    )
    client.headers["X-CSRF-Token"] = session["csrf_token"]
    return session


def new_case(client, profile="merchandise", workspace=None):
    workspace = workspace or successful(
        client.post(
            "/api/workspaces",
            json={"name": "Handover test business", "profile": profile, "seed_demo": True},
        ),
        201,
    )
    prefix = f"/api/workspaces/{workspace['id']}"
    summary = successful(client.get(prefix + "/orders"))["orders"][0]
    path = prefix + f"/orders/{summary['id']}"
    detail = successful(client.get(path))
    window_start = (utcnow() + timedelta(days=2)).replace(microsecond=0)
    return SimpleNamespace(
        client=client,
        workspace=workspace,
        prefix=prefix,
        path=path,
        order=detail,
        revision=detail["accepted_revision"]["number"],
        ready_input={
            "expected_revision": detail["accepted_revision"]["number"],
            "collection_address": "12 Sample Street, Example City",
            "collection_instructions": "Collection desk is open 09:00–17:00. Bring the order number.",
            "collection_window_start": window_start.isoformat(),
            "collection_window_end": (window_start + timedelta(days=3)).isoformat(),
            "delivery_mode": "unavailable",
            "delivery_fee_cents": 0,
            "delivery_area": "",
        },
        collection_at=(window_start + timedelta(hours=2)).isoformat(),
    )


@pytest.fixture
def handover_case(integrated):
    case = new_case(integrated.client)
    with TestClient(integrated.client.app, base_url="http://localhost:5173") as customer:
        case.customer = customer
        yield case


def prepare(case, mode="unavailable", fee=0):
    successful(
        case.client.post(case.path + "/production/start", json={"expected_revision": case.revision})
    )
    data = {
        **case.ready_input,
        "delivery_mode": mode,
        "delivery_fee_cents": fee,
        "delivery_area": "Example City only; address checked by the business"
        if mode != "unavailable"
        else "",
    }
    return successful(case.client.post(case.path + "/handover/ready", json=data))["handover"]


def share(case):
    link = successful(case.client.post(case.path + "/handover/share", json={}))
    path = urlsplit(link["url"]).path
    assert path.startswith("/handover/")
    assert link["url"] in link["notification_text"]
    return "/api" + path, link


def current(case):
    return successful(case.client.get(case.path + "/handover"))["handover"]


def collection_input(case, version, **changes):
    return {
        "expected_version": version,
        "method": "collection",
        "collection_at": case.collection_at,
        "delivery_address": "",
        "contact_phone": "",
        "customer_note": "I will bring the order number.",
        "consent": True,
        **changes,
    }


def delivery_input(version, **changes):
    return {
        "expected_version": version,
        "method": "delivery",
        "collection_at": None,
        "delivery_address": "24 Customer Lane, Example City 12345",
        "contact_phone": "+1 555 010 0200",
        "customer_note": "Please call at the reception desk.",
        "consent": True,
        **changes,
    }


def window_input(case, version, **changes):
    return {
        "expected_version": version,
        "collection_window_start": (
            datetime.fromisoformat(case.ready_input["collection_window_start"]) + timedelta(days=1)
        ).isoformat(),
        "collection_window_end": (
            datetime.fromisoformat(case.ready_input["collection_window_end"]) + timedelta(days=1)
        ).isoformat(),
        **changes,
    }


def assert_completed_cannot_be_paused(case, customer):
    completed = current(case)
    response = case.client.post(case.path + "/hold", json={"reason": "Stale tab tried to pause"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "handover_completed"
    assert current(case) == completed
    # Clearing an old inconsistent hold is still available as a recovery path.
    with session_factory()() as db:
        db.get(Order, case.order["id"]).hold_reason = "Legacy hold on a completed order"
        db.commit()
    cleared = successful(case.client.post(case.path + "/hold", json={"reason": None}))
    assert cleared["status"] == "completed" and cleared["hold_reason"] is None
    assert current(case)["completed_at"] == completed["completed_at"]
    path, link = share(case)
    assert "paused" not in link["notification_text"].lower()
    final = successful(customer.get(path))["handover"]
    assert final["status"] == completed["status"] and final["on_hold"] is False


def pay(case, amount, key="sample-handover-receipt"):
    return case.client.post(
        case.path + "/handover/payment",
        json={
            "amount_cents": amount,
            "reference": "SAMPLE recorded transfer; no real payment",
            "idempotency_key": key,
        },
    )


def quote(case, version, fee, note="Address checked; customer must accept this quote."):
    return case.client.post(
        case.path + "/handover/quote",
        json={"expected_version": version, "delivery_fee_cents": fee, "note": note},
    )


def accept(customer, path, handover, **changes):
    return customer.post(
        path + "/accept-quote",
        json={
            "expected_version": handover["version"],
            "quote_hash": handover["quote_hash"],
            "consent": True,
            **changes,
        },
    )


def inventory(case):
    with session_factory()() as db:
        reservations = [
            (row.id, row.resource_id, row.quantity, row.status)
            for row in db.scalars(
                select(Reservation)
                .where(Reservation.order_id == case.order["id"])
                .order_by(Reservation.id)
            )
        ]
        resources = [
            (row.id, row.total)
            for row in db.scalars(
                select(Resource)
                .where(Resource.workspace_id == case.workspace["id"])
                .order_by(Resource.id)
            )
        ]
    return reservations, resources


def test_finish_requires_started_current_revision_and_never_reopens_production(handover_case):
    case = handover_case
    assert current(case) is None
    assert (
        case.client.post(
            case.path + "/production/finish", json={"expected_revision": case.revision}
        ).status_code
        == 409
    )
    assert case.client.post(case.path + "/handover/ready", json=case.ready_input).status_code == 409
    assert all(row[3] == "held" for row in inventory(case)[0])

    successful(case.client.post(case.path + "/production/start", json={"expected_revision": 1}))
    consumed = inventory(case)
    assert consumed[0] and all(row[3] == "consumed" for row in consumed[0])
    assert (
        case.client.post(
            case.path + "/production/finish", json={"expected_revision": case.revision + 1}
        ).status_code
        == 409
    )
    finished = successful(
        case.client.post(case.path + "/production/finish", json={"expected_revision": 1})
    )
    assert finished["production_status"] == "finished"
    repeated = successful(
        case.client.post(case.path + "/production/finish", json={"expected_revision": 1})
    )
    assert repeated["production_status"] == "finished"
    reopened = case.client.post(case.path + "/production/start", json={"expected_revision": 1})
    assert reopened.status_code in {200, 409}
    detail = successful(case.client.get(case.path))
    assert detail["production_status"] == "finished"
    assert detail["status"] == "ready_for_handover"
    assert any(
        order["id"] == case.order["id"]
        for order in successful(case.client.get(case.prefix + "/production"))["orders"]
    )
    assert inventory(case) == consumed


def test_ready_is_revision_bound_idempotent_and_keeps_an_immutable_configuration(handover_case):
    case = handover_case
    successful(
        case.client.post(case.path + "/production/start", json={"expected_revision": case.revision})
    )
    assert (
        case.client.post(
            case.path + "/handover/ready",
            json={**case.ready_input, "expected_revision": case.revision + 1},
        ).status_code
        == 409
    )
    initial = successful(case.client.post(case.path + "/handover/ready", json=case.ready_input))
    handover = initial["handover"]
    assert handover["status"] == "awaiting_choice"
    assert handover["revision"] == case.revision
    assert handover["config"]["collection_address"] == case.ready_input["collection_address"]
    assert initial["defaults"]["collection_address"] == case.ready_input["collection_address"]
    assert (
        initial["defaults"]["collection_instructions"]
        == case.ready_input["collection_instructions"]
    )
    repeated = successful(case.client.post(case.path + "/handover/ready", json=case.ready_input))
    assert repeated["handover"] == handover
    changed = case.client.post(
        case.path + "/handover/ready",
        json={**case.ready_input, "collection_address": "A different pickup address"},
    )
    assert changed.status_code == 409
    assert current(case) == handover
    detail = successful(case.client.get(case.path))
    assert detail["production_status"] == "finished"
    assert detail["handover"] == handover


@pytest.mark.parametrize("profile", ["merchandise", "bakery"])
def test_collection_requires_consent_recorded_balance_and_keeps_consumed_stock(integrated, profile):
    case = new_case(integrated.client, profile)
    prepared = prepare(case)
    resources_after_production = inventory(case)
    customer_path, _ = share(case)
    with TestClient(integrated.client.app, base_url="http://localhost:5173") as customer:
        assert not customer.cookies
        view = successful(customer.get(customer_path))
        assert view["order"]["is_demo"] is True
        assert view["order"]["revision"] == case.revision
        assert view["business"]["name"] == case.workspace["name"]
        assert view["business"]["timezone"] == case.workspace["timezone"]
        assert view["handover"]["status"] == "awaiting_choice"
        assert (
            customer.post(
                customer_path + "/choose",
                json=collection_input(case, prepared["version"], consent=False),
            ).status_code
            == 422
        )
        confirmed = successful(
            customer.post(
                customer_path + "/choose", json=collection_input(case, prepared["version"])
            )
        )["handover"]
        assert confirmed["status"] == "confirmed" and confirmed["method"] == "collection"
        assert confirmed["confirmed_at"]
        pricing = confirmed["pricing"]
        assert pricing["delivery_fee_cents"] == 0
        assert pricing["total_cents"] == case.order["accepted_revision"]["terms"]["total_cents"]
        assert pricing["balance_cents"] == pricing["total_cents"] - pricing["paid_cents"]
        assert successful(case.client.get(case.path))["status"] == "awaiting_collection"
        assert (
            case.client.post(
                case.path + "/handover/collect", json={"expected_version": confirmed["version"]}
            ).status_code
            == 409
        )
        assert pay(case, pricing["balance_cents"] + 1).status_code in {409, 422}

        partial = successful(pay(case, 100, "sample-partial"))["handover"]
        again = successful(pay(case, 100, "sample-partial"))["handover"]
        assert again["pricing"] == partial["pricing"]
        assert pay(case, 101, "sample-partial").status_code == 409
        paid = successful(pay(case, partial["pricing"]["balance_cents"], "sample-final"))[
            "handover"
        ]
        assert paid["pricing"]["balance_cents"] == 0

        successful(case.client.post(case.path + "/hold", json={"reason": "Check final packaging"}))
        assert successful(customer.get(customer_path))["handover"]["on_hold"] is True
        assert (
            case.client.post(
                case.path + "/handover/collect", json={"expected_version": current(case)["version"]}
            ).status_code
            == 409
        )
        successful(case.client.post(case.path + "/hold", json={"reason": None}))
        collected = successful(
            case.client.post(
                case.path + "/handover/collect",
                json={"expected_version": current(case)["version"]},
            )
        )["handover"]
        assert collected["status"] == "collected" and collected["completed_at"]
        completed_detail = successful(case.client.get(case.path))
        repeat = successful(
            case.client.post(
                case.path + "/handover/collect", json={"expected_version": collected["version"]}
            )
        )["handover"]
        assert repeat == collected
        assert successful(case.client.get(case.path))["events"] == completed_detail["events"]
        assert successful(customer.get(customer_path))["handover"]["status"] == "collected"
        assert (
            customer.post(
                customer_path + "/choose", json=collection_input(case, collected["version"])
            ).status_code
            == 409
        )
        assert successful(case.client.get(case.path))["status"] == "completed"
        assert not any(
            order["id"] == case.order["id"]
            for order in successful(case.client.get(case.prefix + "/production"))["orders"]
        )
        assert inventory(case) == resources_after_production
        assert (
            case.client.post(
                case.path + "/handover/window", json=window_input(case, collected["version"])
            ).status_code
            == 409
        )
        assert_completed_cannot_be_paused(case, customer)
        with session_factory()() as db:
            assert db.scalar(select(func.count()).select_from(Job)) == 0
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(Deposit)
                    .where(
                        Deposit.order_id == case.order["id"],
                        Deposit.idempotency_key == "sample-partial",
                    )
                )
                == 1
            )


@pytest.mark.parametrize("mode,fee", [("included", 0), ("fixed", 750), ("quote", 1250)])
def test_delivery_review_exact_fee_consent_and_paid_dispatch_to_completion(
    handover_case, mode, fee
):
    case = handover_case
    prepared = prepare(case, mode, fee if mode == "fixed" else 0)
    resources_after_production = inventory(case)
    customer_path, _ = share(case)
    requested = successful(
        case.customer.post(customer_path + "/choose", json=delivery_input(prepared["version"]))
    )["handover"]
    assert requested["status"] == "delivery_requested"
    assert requested["delivery_fee_cents"] is None
    assert requested["pricing"]["total_cents"] is None
    assert (
        case.client.post(
            case.path + "/handover/dispatch", json={"expected_version": requested["version"]}
        ).status_code
        == 409
    )
    assert pay(case, 100).status_code == 409
    if mode != "quote":
        assert quote(case, requested["version"], fee + 1).status_code in {409, 422}
    quoted = successful(quote(case, requested["version"], fee))["handover"]
    assert quoted["status"] == "quote_ready" and quoted["quote_hash"]
    assert quoted["pricing"]["delivery_fee_cents"] == fee
    assert quoted["pricing"]["total_cents"] == quoted["pricing"]["order_total_cents"] + fee
    assert accept(case.customer, customer_path, quoted, quote_hash="0" * 64).status_code == 409
    for invalid in (False, "true", 1):
        assert accept(case.customer, customer_path, quoted, consent=invalid).status_code == 422
    confirmed = successful(accept(case.customer, customer_path, quoted))["handover"]
    assert confirmed["status"] == "confirmed" and confirmed["method"] == "delivery"
    assert confirmed["confirmed_at"]
    assert successful(case.client.get(case.path))["status"] == "awaiting_dispatch"
    assert (
        case.client.post(
            case.path + "/handover/dispatch", json={"expected_version": confirmed["version"]}
        ).status_code
        == 409
    )
    paid = successful(pay(case, confirmed["pricing"]["balance_cents"]))["handover"]
    assert paid["pricing"]["balance_cents"] == 0
    if fee:
        change = case.customer.post(
            customer_path + "/choose", json=collection_input(case, paid["version"])
        )
        assert change.status_code == 409
        message = str(change.json()).lower()
        assert "refund" in message or "contact" in message
    assert (
        case.client.post(
            case.path + "/handover/collect", json={"expected_version": paid["version"]}
        ).status_code
        == 409
    )
    assert (
        case.client.post(
            case.path + "/handover/deliver", json={"expected_version": paid["version"]}
        ).status_code
        == 409
    )
    dispatched = successful(
        case.client.post(
            case.path + "/handover/dispatch", json={"expected_version": paid["version"]}
        )
    )["handover"]
    assert dispatched["status"] == "out_for_delivery" and dispatched["dispatched_at"]
    assert (
        case.client.post(
            case.path + "/handover/window", json=window_input(case, dispatched["version"])
        ).status_code
        == 409
    )
    assert (
        successful(
            case.client.post(
                case.path + "/handover/dispatch", json={"expected_version": dispatched["version"]}
            )
        )["handover"]
        == dispatched
    )
    assert successful(case.client.get(case.path))["status"] == "out_for_delivery"
    assert (
        case.customer.post(
            customer_path + "/choose",
            json=delivery_input(dispatched["version"], delivery_address="Different address"),
        ).status_code
        == 409
    )
    assert quote(case, dispatched["version"], fee).status_code == 409
    completed = successful(
        case.client.post(
            case.path + "/handover/deliver", json={"expected_version": dispatched["version"]}
        )
    )["handover"]
    assert completed["status"] == "delivered" and completed["completed_at"]
    assert (
        successful(
            case.client.post(
                case.path + "/handover/deliver", json={"expected_version": completed["version"]}
            )
        )["handover"]
        == completed
    )
    assert successful(case.customer.get(customer_path))["handover"]["status"] == "delivered"
    assert successful(case.client.get(case.path))["status"] == "completed"
    assert inventory(case) == resources_after_production
    assert (
        case.client.post(
            case.path + "/handover/window", json=window_input(case, completed["version"])
        ).status_code
        == 409
    )
    assert_completed_cannot_be_paused(case, case.customer)


def test_revised_quote_and_customer_choice_revoke_prior_consent(handover_case):
    case = handover_case
    prepared = prepare(case, "quote")
    customer_path, _ = share(case)
    requested = successful(
        case.customer.post(customer_path + "/choose", json=delivery_input(prepared["version"]))
    )["handover"]
    first_quote = successful(quote(case, requested["version"], 900))["handover"]
    second_quote = successful(quote(case, first_quote["version"], 600))["handover"]
    assert first_quote["quote_hash"] != second_quote["quote_hash"]
    assert accept(case.customer, customer_path, first_quote).status_code == 409
    assert (
        accept(
            case.customer, customer_path, second_quote, quote_hash=first_quote["quote_hash"]
        ).status_code
        == 409
    )
    confirmed = successful(accept(case.customer, customer_path, second_quote))["handover"]
    assert quote(case, confirmed["version"], 2000).status_code == 409
    assert (
        case.customer.post(
            customer_path + "/choose", json=collection_input(case, second_quote["version"])
        ).status_code
        == 409
    )
    collection = successful(
        case.customer.post(
            customer_path + "/choose", json=collection_input(case, confirmed["version"])
        )
    )["handover"]
    assert collection["method"] == "collection" and collection["status"] == "confirmed"
    assert collection["delivery_address"] is None
    assert collection["delivery_fee_cents"] == 0 and collection["quote_hash"] is None
    assert accept(case.customer, customer_path, second_quote).status_code == 409
    requested_again = successful(
        case.customer.post(customer_path + "/choose", json=delivery_input(collection["version"]))
    )["handover"]
    assert requested_again["status"] == "delivery_requested"
    assert requested_again["collection_at"] is None
    assert requested_again["confirmed_at"] is None
    assert requested_again["delivery_fee_cents"] is None and requested_again["quote_hash"] is None


@pytest.mark.parametrize("invalid_time", ["naive", "past", "before_window", "after_window"])
def test_collection_validates_actual_timezone_and_available_window(handover_case, invalid_time):
    case = handover_case
    prepared = prepare(case)
    customer_path, _ = share(case)
    start = datetime.fromisoformat(case.ready_input["collection_window_start"])
    end = datetime.fromisoformat(case.ready_input["collection_window_end"])
    value = {
        "naive": datetime.fromisoformat(case.collection_at).replace(tzinfo=None),
        "past": utcnow() - timedelta(hours=1),
        "before_window": start - timedelta(seconds=1),
        "after_window": end + timedelta(seconds=1),
    }[invalid_time]
    result = case.customer.post(
        customer_path + "/choose",
        json=collection_input(case, prepared["version"], collection_at=value.isoformat()),
    )
    assert result.status_code in {409, 422}
    assert current(case)["status"] == "awaiting_choice"


@pytest.mark.parametrize(
    "changes", [{"delivery_address": ""}, {"contact_phone": ""}, {"delivery_fee_cents": 1}]
)
def test_delivery_rejects_incomplete_details_and_customer_supplied_prices(handover_case, changes):
    case = handover_case
    prepared = prepare(case, "fixed", 750)
    customer_path, _ = share(case)
    response = case.customer.post(
        customer_path + "/choose", json=delivery_input(prepared["version"], **changes)
    )
    assert response.status_code == 422
    assert current(case)["status"] == "awaiting_choice"


def test_collection_only_business_cannot_be_forced_to_deliver(handover_case):
    case = handover_case
    prepared = prepare(case)
    customer_path, _ = share(case)
    assert case.customer.post(
        customer_path + "/choose", json=delivery_input(prepared["version"])
    ).status_code in {409, 422}
    assert current(case)["status"] == "awaiting_choice"


def test_finished_order_cannot_accept_new_proposals_or_customer_changes(handover_case):
    case = handover_case
    option = next(r for r in case.order["revisions"] if r["feasible"] and r["status"] == "proposed")
    original_link = successful(
        case.client.post(case.path + f"/proposals/{option['id']}/share", json={})
    )
    token = original_link["url"].rsplit("/", 1)[1]
    prepare(case)
    assert (
        case.client.post(
            case.path + "/messages", json={"body": "Please change the finished order"}
        ).status_code
        == 409
    )
    terms = case.order["accepted_revision"]["terms"]
    assert (
        case.client.post(
            case.path + "/proposals",
            json={key: terms[key] for key in ("quantity", "variant", "sizes", "pickup_at")},
        ).status_code
        == 409
    )
    assert case.client.post(case.path + f"/proposals/{option['id']}/share").status_code == 409
    assert case.customer.post(
        f"/api/customer/{token}/approve",
        json={"terms_hash": original_link["terms_hash"], "consent": True},
    ).status_code in {409, 410}
    assert case.customer.post(
        f"/api/customer/{token}/request-change",
        json={"body": "Double the quantity after production"},
    ).status_code in {409, 410}
    with session_factory()() as db:
        order = db.get(Order, case.order["id"])
        assert order.accepted_revision_id == case.order["accepted_revision"]["id"]
        assert order.production_status == "finished"
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        assert db.scalar(
            select(func.count())
            .select_from(SourceMessage)
            .where(SourceMessage.order_id == order.id)
        ) == len(case.order["messages"])


@pytest.mark.parametrize("previous_attempts", [0, 1])
def test_unfinished_analysis_cannot_reserve_paid_attempts_after_production(
    handover_case, previous_attempts
):
    case = handover_case
    get_settings().agent_mode = "bedrock"
    queued = successful(
        case.client.post(case.path + "/messages", json={"body": "Please add more shirts"}),
        202,
    )["job"]
    if previous_attempts:
        # A worker lease expired while the owner finished production. Retrying
        # that lease must not reserve a second paid model attempt.
        with session_factory()() as db:
            job = db.get(Job, queued["id"])
            job.status = "running"
            job.attempts = 1
            job.leased_until = utcnow() - timedelta(seconds=1)
            job.lease_token = "00000000-0000-0000-0000-000000000001"
            db.add(AgentDailyUsage(day=utcnow().date(), attempts=1))
            db.commit()
    prepare(case)
    with session_factory()() as db:
        assert claim_next(db) is None
        db.commit()
        job = db.get(Job, queued["id"])
        assert job.status == "failed"
        assert job.attempts == previous_attempts
        assert job.lease_token is None and job.leased_until is None
        assert "production" in job.error.lower()
        assert (db.scalar(select(func.sum(AgentDailyUsage.attempts))) or 0) == previous_attempts
        assert (
            db.get(Order, case.order["id"]).accepted_revision_id
            == case.order["accepted_revision"]["id"]
        )


def test_private_link_rotation_keeps_customer_choice_and_hides_internal_data(handover_case):
    case = handover_case
    prepared = prepare(case, "quote")
    first_path, first_link = share(case)
    requested = successful(
        case.customer.post(first_path + "/choose", json=delivery_input(prepared["version"]))
    )["handover"]
    quoted = successful(quote(case, requested["version"], 500))["handover"]
    new_path, replacement = share(case)
    assert replacement["url"] != first_link["url"]
    assert case.customer.get(first_path).status_code == 410
    assert (
        case.customer.post(
            first_path + "/choose", json=collection_input(case, quoted["version"])
        ).status_code
        == 410
    )
    view = successful(case.customer.get(new_path))
    assert view["handover"]["status"] == "quote_ready"
    assert view["handover"]["quote_hash"] == quoted["quote_hash"]
    assert view["handover"]["delivery_address"] == delivery_input(1)["delivery_address"]
    forbidden = {
        "workspace_id",
        "user_id",
        "provider_subject",
        "customer_email",
        "messages",
        "events",
        "resource_requirements",
        "resources",
        "deposits",
        "token_hash",
        "recorded_by",
    }

    def keys(value):
        if isinstance(value, dict):
            yield from value
            for item in value.values():
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    assert forbidden.isdisjoint(keys(view))
    detail = successful(case.client.get(case.path))
    raw_token = replacement["url"].rsplit("/", 1)[1]
    assert raw_token not in str(detail)
    assert raw_token not in str(successful(case.client.get(case.path + "/handover")))
    assert "no-store" in case.customer.get(new_path).headers["cache-control"]
    assert (
        case.customer.post(
            new_path + "/accept-quote",
            json={
                "expected_version": quoted["version"],
                "quote_hash": quoted["quote_hash"],
                "consent": True,
            },
            headers={"Origin": "https://unrelated.example"},
        ).status_code
        == 403
    )


def test_expired_handover_link_cannot_mutate_and_owner_can_replace_it(handover_case):
    case = handover_case
    prepared = prepare(case)
    path, link = share(case)
    token = link["url"].rsplit("/", 1)[1]
    with session_factory()() as db:
        stored = db.scalar(select(HandoverLink).where(HandoverLink.token_hash == digest(token)))
        assert stored is not None and stored.token_hash != token
        stored.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert case.customer.get(path).status_code == 410
    assert (
        case.customer.post(
            path + "/choose", json=collection_input(case, prepared["version"])
        ).status_code
        == 410
    )
    unchanged = current(case)
    assert unchanged["status"] == "awaiting_choice"
    assert unchanged["link_active"] is False
    replacement, _ = share(case)
    assert replacement != path
    assert successful(case.customer.get(replacement))["handover"]["id"] == unchanged["id"]


def test_expired_collection_window_can_be_recovered_without_changing_business_terms(
    handover_case, monkeypatch
):
    case = handover_case
    prepared = prepare(case)
    old_path, _ = share(case)
    now = datetime.fromisoformat(prepared["config"]["collection_window_end"]) + timedelta(days=15)
    monkeypatch.setattr(handover_service, "utcnow", lambda: now)
    assert case.customer.get(old_path).status_code == 410
    new_start = now + timedelta(hours=1)
    new_end = now + timedelta(days=2)
    values = window_input(
        case,
        prepared["version"],
        collection_window_start=new_start.isoformat(),
        collection_window_end=new_end.isoformat(),
    )
    assert (
        case.client.post(
            case.path + "/handover/window",
            json={**values, "expected_version": prepared["version"] + 1},
        ).status_code
        == 409
    )
    updated = successful(case.client.post(case.path + "/handover/window", json=values))["handover"]
    assert updated["id"] == prepared["id"] and updated["revision"] == prepared["revision"]
    assert updated["version"] == prepared["version"] + 1
    assert updated["ready_at"] == prepared["ready_at"]
    for key in (
        "collection_address",
        "collection_instructions",
        "delivery_mode",
        "delivery_fee_cents",
        "delivery_area",
        "timezone",
    ):
        assert updated["config"][key] == prepared["config"][key]
    assert datetime.fromisoformat(updated["config"]["collection_window_start"]) == new_start
    assert datetime.fromisoformat(updated["config"]["collection_window_end"]) == new_end
    replacement, _ = share(case)
    assert case.customer.get(old_path).status_code == 410
    assert successful(case.customer.get(replacement))["handover"]["status"] == "awaiting_choice"
    confirmed = successful(
        case.customer.post(
            replacement + "/choose",
            json=collection_input(
                case, updated["version"], collection_at=(new_start + timedelta(hours=1)).isoformat()
            ),
        )
    )["handover"]
    assert confirmed["status"] == "confirmed" and confirmed["method"] == "collection"


def test_changing_confirmed_collection_window_requires_fresh_consent_and_preserves_receipts(
    handover_case,
):
    case = handover_case
    prepared = prepare(case)
    path, _ = share(case)
    confirmed = successful(
        case.customer.post(path + "/choose", json=collection_input(case, prepared["version"]))
    )["handover"]
    paid = successful(pay(case, 100, "sample-before-window-change"))["handover"]
    values = window_input(case, paid["version"])
    updated = successful(case.client.post(case.path + "/handover/window", json=values))["handover"]
    assert updated["status"] == "awaiting_choice" and updated["method"] is None
    assert updated["collection_at"] is None and updated["confirmed_at"] is None
    assert updated["delivery_fee_cents"] is None
    assert updated["pricing"]["paid_cents"] == paid["pricing"]["paid_cents"]
    assert updated["pricing"]["order_total_cents"] == confirmed["pricing"]["order_total_cents"]
    assert updated["version"] == paid["version"] + 1
    assert (
        case.client.post(
            case.path + "/handover/collect", json={"expected_version": updated["version"]}
        ).status_code
        == 409
    )
    assert successful(case.customer.get(path))["handover"]["status"] == "awaiting_choice"
    next_time = datetime.fromisoformat(values["collection_window_start"]) + timedelta(hours=1)
    reconfirmed = successful(
        case.customer.post(
            path + "/choose",
            json=collection_input(case, updated["version"], collection_at=next_time.isoformat()),
        )
    )["handover"]
    assert reconfirmed["pricing"] == paid["pricing"]
    assert reconfirmed["collection_at"] != confirmed["collection_at"]


def test_window_change_preserves_delivery_quote_but_invalidates_stale_quote_consent(handover_case):
    case = handover_case
    prepared = prepare(case, "fixed", 500)
    path, _ = share(case)
    requested = successful(
        case.customer.post(path + "/choose", json=delivery_input(prepared["version"]))
    )["handover"]
    quoted = successful(quote(case, requested["version"], 500))["handover"]
    values = window_input(case, quoted["version"])
    updated = successful(case.client.post(case.path + "/handover/window", json=values))["handover"]
    assert updated["status"] == "quote_ready" and updated["method"] == "delivery"
    assert updated["version"] == quoted["version"] + 1
    assert updated["quote_hash"] != quoted["quote_hash"]
    for key in (
        "delivery_fee_cents",
        "delivery_address",
        "contact_phone",
        "customer_note",
        "quote_note",
        "pricing",
    ):
        assert updated[key] == quoted[key]
    assert accept(case.customer, path, quoted).status_code == 409
    assert accept(case.customer, path, updated, quote_hash=quoted["quote_hash"]).status_code == 409
    assert successful(accept(case.customer, path, updated))["handover"]["status"] == "confirmed"


def test_window_edit_validates_dates_and_cannot_amend_address_or_fee(handover_case):
    case = handover_case
    prepared = prepare(case, "fixed", 500)
    values = window_input(case, prepared["version"])
    invalid = (
        {"collection_window_start": "2026-09-13T12:00:00"},
        {"collection_window_end": values["collection_window_start"]},
        {"collection_window_end": (utcnow() + timedelta(days=31)).isoformat()},
        {"delivery_fee_cents": 1},
        {"collection_address": "Another address"},
    )
    for changes in invalid:
        response = case.client.post(case.path + "/handover/window", json={**values, **changes})
        assert response.status_code == 422, response.text
        assert current(case) == prepared


def test_handover_snapshots_business_timezone_independent_of_production_and_later_settings(
    handover_case,
):
    case = handover_case
    assert case.order["accepted_revision"]["terms"]["timezone"] == "America/New_York"
    successful(case.client.patch(case.prefix, json={"timezone": "Asia/Kolkata"}))
    prepared = prepare(case)
    path, _ = share(case)
    assert prepared["config"]["timezone"] == "Asia/Kolkata"
    assert successful(case.customer.get(path))["business"]["timezone"] == "Asia/Kolkata"
    successful(case.client.patch(case.prefix, json={"timezone": "Europe/London"}))
    persisted = current(case)
    customer = successful(case.customer.get(path))
    assert persisted["config"]["timezone"] == "Asia/Kolkata"
    assert customer["business"]["timezone"] == persisted["config"]["timezone"]
    assert (
        persisted["config"]["collection_window_start"]
        == prepared["config"]["collection_window_start"]
    )
    assert (
        persisted["config"]["collection_window_end"] == prepared["config"]["collection_window_end"]
    )


def test_finished_order_legacy_deposit_route_cannot_bypass_handover_consent(handover_case):
    case = handover_case
    prepare(case, "fixed", 500)
    before = current(case)["pricing"]
    response = case.client.post(
        case.path + "/deposits",
        json={
            "amount_cents": 100,
            "reference": "SAMPLE final transfer through obsolete deposit form",
            "idempotency_key": "obsolete-form-transfer",
        },
    )
    assert response.status_code == 409
    assert current(case)["pricing"] == before


def test_operator_can_finish_but_cannot_read_or_change_handover_finances(handover_case):
    case = handover_case
    login(case.client, "operator@example.test")
    login(case.client, "owner@example.com")
    successful(
        case.client.post(
            case.prefix + "/members", json={"email": "operator@example.test", "role": "operator"}
        ),
        201,
    )
    successful(case.client.post(case.path + "/production/start", json={"expected_revision": 1}))
    login(case.client, "operator@example.test")
    ticket = successful(
        case.client.post(case.path + "/production/finish", json={"expected_revision": 1})
    )
    assert ticket["production_status"] == "finished"
    for field in (
        "balance_cents",
        "deposit_paid_cents",
        "handover",
        "delivery_address",
        "customer_email",
    ):
        assert field not in ticket
    assert "total_cents" not in ticket["terms"]
    assert case.client.get(case.path + "/handover").status_code == 403
    for action, body in {
        "ready": case.ready_input,
        "share": {},
        "quote": {"expected_version": 1, "delivery_fee_cents": 0, "note": "Checked"},
        "payment": {"amount_cents": 1, "reference": "sample", "idempotency_key": "sample"},
        "window": window_input(case, 1),
        "dispatch": {"expected_version": 1},
        "collect": {"expected_version": 1},
        "deliver": {"expected_version": 1},
    }.items():
        assert case.client.post(case.path + f"/handover/{action}", json=body).status_code == 403
    login(case.client, "outsider@example.test")
    assert case.client.get(case.path + "/handover").status_code == 404
    assert case.client.post(case.path + "/handover/ready", json=case.ready_input).status_code == 404
    assert case.client.post(case.path + "/handover/share", json={}).status_code == 404
    assert (
        case.client.post(
            case.path + "/production/finish", json={"expected_revision": 1}
        ).status_code
        == 404
    )


@pytest.mark.parametrize("revoke", ["expiry", "rotation"])
def test_reviewer_revocation_also_revokes_handover_customer_capability(integrated, revoke):
    client = integrated.client
    successful(client.post("/api/auth/logout"))
    client.headers.pop("X-CSRF-Token", None)
    settings = get_settings()
    token = "synthetic-handover-reviewer-capability-no-real-access"
    settings.reviewer_token_hash = digest(token)
    settings.reviewer_expires_at = utcnow() + timedelta(days=10)
    reviewer = successful(client.post("/api/auth/reviewer", json={"token": token}))
    client.headers["X-CSRF-Token"] = reviewer["csrf_token"]
    workspace = next(w for w in reviewer["workspaces"] if w["profile"] == "merchandise")
    case = new_case(client, workspace=workspace)
    prepared = prepare(case)
    path, _ = share(case)
    if revoke == "expiry":
        settings.reviewer_expires_at = utcnow() - timedelta(seconds=1)
    else:
        settings.reviewer_token_hash = "c" * 64
    with TestClient(client.app, base_url="http://localhost:5173") as customer:
        assert customer.get(path).status_code == 410
        assert (
            customer.post(
                path + "/choose", json=collection_input(case, prepared["version"])
            ).status_code
            == 410
        )
