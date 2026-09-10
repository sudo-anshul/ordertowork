"""Route authorization/response isolation beyond service-level tenant assertions."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ordertowork.api.orders import router
from ordertowork.db import Base, get_db
from ordertowork.models.core import Membership, User, Workspace
from ordertowork.models.domain import Order, OrderRevision
from ordertowork.services.auth import Actor, get_actor
from ordertowork.services.profiles import seed_workspace
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def domain_api():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        owner = User(provider_subject="test-owner", email="owner@example.test", name="Owner")
        operator = User(
            provider_subject="test-operator", email="operator@example.test", name="Operator"
        )
        outsider = User(provider_subject="test-outsider", email="other@example.test", name="Other")
        workspace = Workspace(name="Business", profile="merchandise", is_demo=True)
        db.add_all([owner, operator, outsider, workspace])
        db.flush()
        db.add_all(
            [
                Membership(workspace_id=workspace.id, user_id=owner.id, role="owner"),
                Membership(workspace_id=workspace.id, user_id=operator.id, role="operator"),
            ]
        )
        seed_workspace(db, workspace)
        db.commit()
        order = db.scalar(select(Order).where(Order.workspace_id == workspace.id))
        option = db.scalar(
            select(OrderRevision).where(
                OrderRevision.order_id == order.id,
                OrderRevision.feasible.is_(True),
                OrderRevision.status == "proposed",
            )
        )
        context = SimpleNamespace(
            factory=factory,
            owner=owner,
            operator=operator,
            outsider=outsider,
            workspace=workspace,
            order=order,
            option=option,
            actor=owner,
        )
    app = FastAPI()
    app.include_router(router, prefix="/api")

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_actor] = lambda: Actor(user=context.actor, csrf_token="test")
    with TestClient(app) as client:
        context.client = client
        yield context
    engine.dispose()


def test_operator_cannot_read_owner_data_or_mutate_commercial_terms(domain_api):
    context = domain_api
    context.actor = context.operator
    base = f"/api/workspaces/{context.workspace.id}"
    for path in ("/orders", f"/orders/{context.order.id}", "/resources"):
        assert context.client.get(base + path).status_code == 403
    payment = context.client.post(
        base + f"/orders/{context.order.id}/deposits",
        json={"amount_cents": 100, "reference": "test", "idempotency_key": "test"},
    )
    assert payment.status_code == 403
    queue = context.client.get(base + "/production")
    assert queue.status_code == 200
    assert len(queue.json()["orders"]) == 1
    ticket = context.client.get(base + f"/orders/{context.order.id}/ticket")
    assert ticket.status_code == 200
    assert "total_cents" not in ticket.json()["terms"]
    assert "deposit_paid_cents" not in ticket.json()
    started = context.client.post(base + f"/orders/{context.order.id}/production/start", json={})
    assert started.status_code == 200
    assert started.json()["production_status"] == "started"
    assert "messages" not in started.json()
    assert "customer_email" not in started.json()
    assert "balance_cents" not in started.json()


def test_cross_tenant_reads_and_writes_fail_before_resource_access(domain_api):
    context = domain_api
    context.actor = context.outsider
    base = f"/api/workspaces/{context.workspace.id}"
    assert context.client.get(base + "/orders").status_code == 404
    assert context.client.get(base + "/production").status_code == 404
    assert context.client.get(base + f"/orders/{context.order.id}/ticket").status_code == 404
    response = context.client.post(
        base + f"/orders/{context.order.id}/proposals/{context.option.id}/share", json={}
    )
    assert response.status_code == 404


def test_public_approval_requires_boolean_true_and_exact_digest(domain_api):
    context = domain_api
    base = f"/api/workspaces/{context.workspace.id}/orders/{context.order.id}"
    response = context.client.post(base + f"/proposals/{context.option.id}/share", json={})
    assert response.status_code == 200
    token = response.json()["url"].rsplit("/", 1)[1]
    for consent in (False, "true", 1):
        result = context.client.post(
            f"/api/customer/{token}/approve",
            json={"terms_hash": context.option.terms_hash, "consent": consent},
        )
        assert result.status_code == 422
    result = context.client.post(
        f"/api/customer/{token}/approve",
        json={"terms_hash": context.option.terms_hash, "consent": True},
    )
    assert result.status_code == 200
    assert result.json()["status"] == "approved"
    again = context.client.post(
        f"/api/customer/{token}/approve",
        json={"terms_hash": context.option.terms_hash, "consent": True},
    )
    assert again.status_code == 200
    assert again.json()["status"] == "already_approved"
