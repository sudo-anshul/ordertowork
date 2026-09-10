"""Guest sessions exercise the real workflow without escaping isolation or spend limits."""

import asyncio
from datetime import datetime, timedelta

import pytest
from ordertowork.config import get_settings
from ordertowork.db import session_factory, utcnow
from ordertowork.models.auth import AuthSession, DemoDailyUsage
from ordertowork.models.core import User, Workspace
from ordertowork.models.domain import OrderRevision, SourceMessage
from ordertowork.models.jobs import AgentDailyUsage, Job
from ordertowork.services import auth, jobs
from ordertowork.worker import process_one
from sqlalchemy import func, select


@pytest.fixture
def guest(integrated):
    client = integrated.client
    assert client.post("/api/auth/logout").status_code == 200
    get_settings().demo_enabled = True
    client.headers.pop("X-CSRF-Token", None)
    return client


def start_demo(client):
    response = client.post("/api/auth/demo")
    assert response.status_code == 200, response.text
    payload = response.json()
    client.headers["X-CSRF-Token"] = payload["csrf_token"]
    return payload


def first_order(client, workspace):
    prefix = f"/api/workspaces/{workspace['id']}"
    orders = client.get(prefix + "/orders")
    assert orders.status_code == 200, orders.text
    order_id = orders.json()["orders"][0]["id"]
    return prefix + f"/orders/{order_id}", client.get(prefix + f"/orders/{order_id}").json()


def share_option(client, workspace):
    base, order = first_order(client, workspace)
    option = next(r for r in order["revisions"] if r["status"] == "proposed" and r["feasible"])
    response = client.post(base + f"/proposals/{option['id']}/share")
    assert response.status_code == 200, response.text
    link = response.json()
    return base, option, link, link["url"].rsplit("/", 1)[1]


def test_guest_entry_is_isolated_reusable_and_creates_no_model_job(guest):
    assert guest.get("/api/auth/config").json()["demo_enabled"] is True
    first = start_demo(guest)
    assert first["auth_method"] == "demo"
    assert first["demo"]["max_agent_jobs_per_workspace"] == 2
    assert first["user"]["email_verified"] is False
    assert first["user"]["is_platform_admin"] is False
    assert {w["profile"] for w in first["workspaces"]} == {"bakery", "merchandise"}
    assert {w["demo_expires_at"] for w in first["workspaces"]} == {first["demo"]["expires_at"]}
    cookie = guest.cookies.get("otw_session")
    again = start_demo(guest)
    assert again == first
    assert guest.cookies.get("otw_session") == cookie
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        assert db.get(DemoDailyUsage, utcnow().date()).sessions == 1
        user = db.get(User, first["user"]["id"])
        # A mistaken admin allowlist entry must never promote a guest.
        get_settings().platform_admin_subjects = user.provider_subject
        assert auth.platform_admin(user) is False
    assert guest.get("/api/platform/overview").status_code == 403
    guest.cookies.clear()
    second = start_demo(guest)
    assert second["user"]["id"] != first["user"]["id"]
    assert guest.get(f"/api/workspaces/{first['workspaces'][0]['id']}/orders").status_code == 404


def test_entry_is_disabled_by_default_and_rejects_identity_or_foreign_origin(guest):
    get_settings().demo_enabled = False
    assert guest.post("/api/auth/demo").status_code == 404
    get_settings().demo_enabled = True
    assert guest.post("/api/auth/demo", json={"email": "owner@example.com"}).status_code == 422
    assert (
        guest.post("/api/auth/demo", headers={"Origin": "https://other.example"}).status_code == 403
    )
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(DemoDailyUsage)) == 0


def test_active_business_session_is_preserved(integrated):
    client = integrated.client
    get_settings().demo_enabled = True
    before = client.get("/api/auth/me").json()
    cookie = client.cookies.get("otw_session")
    response = client.post("/api/auth/demo")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "business_session_active"
    assert client.cookies.get("otw_session") == cookie
    assert client.get("/api/auth/me").json() == before
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(DemoDailyUsage)) == 0


def test_guest_creation_shared_quota_and_csrf_are_enforced(guest):
    get_settings().max_daily_demo_sessions = 1
    session = start_demo(guest)
    workspace = session["workspaces"][0]
    base, _ = first_order(guest, workspace)
    assert (
        guest.post(
            base + "/messages",
            json={"body": "Please make 36 cupcakes."},
            headers={"X-CSRF-Token": "wrong"},
        ).status_code
        == 403
    )
    guest.cookies.clear()
    assert guest.post("/api/auth/demo").status_code == 429
    with session_factory()() as db:
        assert db.get(DemoDailyUsage, utcnow().date()).sessions == 1
        assert db.scalar(select(func.count()).select_from(Workspace)) == 2


def test_guest_cannot_expand_account_upload_or_change_configuration(guest):
    session = start_demo(guest)
    workspace = session["workspaces"][0]
    prefix = f"/api/workspaces/{workspace['id']}"
    base, order = first_order(guest, workspace)
    member = guest.get(prefix + "/members").json()["members"][0]
    resources = guest.get(prefix + "/resources").json()
    product = resources["products"][0]
    resource = resources["resources"][0]
    operations = [
        ("post", "/api/workspaces", {"name": "Escape", "profile": "bakery"}),
        ("patch", prefix, {"name": "Changed"}),
        ("delete", prefix, {"confirm_name": workspace["name"]}),
        ("post", prefix + "/members", {"email": "owner@example.com"}),
        ("patch", prefix + f"/members/{member['id']}", {"role": "operator"}),
        ("delete", prefix + f"/members/{member['id']}", None),
        ("patch", prefix + f"/resources/{resource['id']}", {"total": 100}),
        (
            "post",
            prefix + "/resources",
            {"kind": "stock", "key": "new", "label": "Test", "unit": "each", "total": 10},
        ),
        ("patch", prefix + f"/products/{product['id']}", {"unit_price_cents": 1}),
        (
            "post",
            prefix + "/orders",
            {
                "customer_name": "Escape",
                "product_id": product["id"],
                "quantity": 1,
                "variant": product["variants"][0],
                "pickup_at": (utcnow() + timedelta(days=10)).isoformat(),
            },
        ),
    ]
    for method, path, body in operations:
        response = guest.request(method, path, json=body)
        assert response.status_code == 403, (path, response.text)
        assert response.json()["detail"]["code"] == "demo_restricted"
    upload = guest.post(base + "/files", files={"file": ("test.txt", b"test", "text/plain")})
    assert upload.status_code == 403
    assert guest.get(base).json()["id"] == order["id"]


def test_prepared_guest_workflow_approves_deposit_and_production_without_model(guest):
    session = start_demo(guest)
    workspace = next(w for w in session["workspaces"] if w["profile"] == "merchandise")
    base, option, link, token = share_option(guest, workspace)
    assert auth.aware(datetime.fromisoformat(link["expires_at"])) <= auth.aware(
        datetime.fromisoformat(session["demo"]["expires_at"])
    )
    customer = guest.get(f"/api/customer/{token}")
    assert customer.status_code == 200
    approval = guest.post(
        f"/api/customer/{token}/approve", json={"terms_hash": option["terms_hash"], "consent": True}
    )
    assert approval.status_code == 200, approval.text
    detail = guest.get(base).json()
    top_up = option["terms"]["required_deposit_cents"] - detail["deposit_paid_cents"]
    if top_up > 0:
        assert (
            guest.post(
                base + "/deposits",
                json={
                    "amount_cents": top_up,
                    "reference": "Synthetic demo deposit",
                    "idempotency_key": "demo-deposit",
                },
            ).status_code
            == 200
        )
    start = guest.post(base + "/production/start", json={"expected_revision": option["number"]})
    assert start.status_code == 200, start.text
    assert start.json()["production_status"] == "started"


@pytest.mark.parametrize("disable", [False, True])
def test_guest_expiry_or_disable_blocks_public_links_and_queued_paid_jobs(guest, disable):
    get_settings().agent_mode = "bedrock"
    session = start_demo(guest)
    workspace = session["workspaces"][0]
    base, option, _, token = share_option(guest, workspace)
    queued = guest.post(base + "/messages", json={"body": "Please change the quantity to 36."})
    assert queued.status_code == 202
    with session_factory()() as db:
        if disable:
            get_settings().demo_enabled = False
        else:
            db.get(Workspace, workspace["id"]).demo_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        assert jobs.claim_next(db) is None
        db.commit()
        assert db.get(Job, queued.json()["job"]["id"]).status == "failed"
        assert db.scalar(select(func.count()).select_from(AgentDailyUsage)) == 0
        assert db.get(DemoDailyUsage, utcnow().date()).bedrock_attempts == 0
        assert db.get(Workspace, workspace["id"]).demo_agent_attempts == 0
    assert guest.get(base).status_code in (401, 410)
    assert guest.get(f"/api/customer/{token}").status_code == 410
    assert (
        guest.post(
            f"/api/customer/{token}/approve",
            json={"terms_hash": option["terms_hash"], "consent": True},
        ).status_code
        == 410
    )
    assert (
        guest.post(
            f"/api/customer/{token}/request-change", json={"body": "Another request"}
        ).status_code
        == 410
    )


def test_guest_session_expiry_and_logout_do_not_redirect_to_cognito(guest):
    first = start_demo(guest)
    with session_factory()() as db:
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == first["user"]["id"]))
        session.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert guest.get("/api/auth/me").status_code == 401
    start_demo(guest)
    result = guest.post("/api/auth/logout")
    assert result.status_code == 200
    assert result.json()["logout_url"] is None
    assert guest.get("/api/auth/me").status_code == 401


def test_guest_analysis_cap_applies_to_refresh_messages_and_retries(guest):
    session = start_demo(guest)
    base, order = first_order(guest, session["workspaces"][0])
    ids = []
    for _ in range(2):
        queued = guest.post(base + "/messages", json={"body": "Please change the quantity to 36."})
        assert queued.status_code == 202, queued.text
        ids.append(queued.json()["job"]["id"])
        assert asyncio.run(process_one()) is True
    assert start_demo(guest)["user"]["id"] == session["user"]["id"]
    with session_factory()() as db:
        before = db.scalar(
            select(func.count())
            .select_from(SourceMessage)
            .where(SourceMessage.order_id == order["id"])
        )
        assert db.get(Workspace, session["workspaces"][0]["id"]).demo_agent_attempts == 2
        db.get(Job, ids[0]).status = "failed"
        db.commit()
    denied = guest.post(base + "/messages", json={"body": "Another request"})
    assert denied.status_code == 429
    retry = guest.post(f"/api/workspaces/{session['workspaces'][0]['id']}/jobs/{ids[0]}/retry")
    assert retry.status_code == 429
    with session_factory()() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(SourceMessage)
                .where(SourceMessage.order_id == order["id"])
            )
            == before
        )


def test_expiry_during_model_run_does_not_apply_agent_result(guest, monkeypatch):
    from ordertowork import worker
    from ordertowork.services.agent import reference_interpretation
    from ordertowork.services.orders import order_snapshot

    get_settings().agent_mode = "bedrock"
    session = start_demo(guest)
    workspace_id = session["workspaces"][0]["id"]
    base, order = first_order(guest, session["workspaces"][0])
    queued = guest.post(base + "/messages", json={"body": "Please change the quantity to 36."})
    assert queued.status_code == 202
    with session_factory()() as db:
        revisions_before = db.scalar(
            select(func.count())
            .select_from(OrderRevision)
            .where(OrderRevision.order_id == order["id"])
        )

    async def expire_instead_of_model(workspace_id, order_id, body, events, **kwargs):
        with session_factory()() as db:
            snapshot = order_snapshot(db, workspace_id, order_id)
            db.get(Workspace, workspace_id).demo_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        return reference_interpretation(snapshot, body)

    monkeypatch.setattr(worker, "interpret_with_strands", expire_instead_of_model)
    assert asyncio.run(process_one()) is True
    with session_factory()() as db:
        assert db.get(Job, queued.json()["job"]["id"]).status == "failed"
        assert (
            db.scalar(
                select(func.count())
                .select_from(OrderRevision)
                .where(OrderRevision.order_id == order["id"])
            )
            == revisions_before
        )
        assert db.get(Workspace, workspace_id).demo_agent_attempts == 1


def test_ordinary_seeded_workspaces_keep_business_permissions(integrated):
    client = integrated.client
    get_settings().demo_enabled = False
    response = client.post(
        "/api/workspaces", json={"name": "Business samples", "profile": "bakery", "seed_demo": True}
    )
    assert response.status_code == 201
    workspace = response.json()
    assert workspace["is_demo"] is True and workspace["demo_expires_at"] is None
    assert (
        client.patch(
            f"/api/workspaces/{workspace['id']}", json={"name": "Still my business"}
        ).status_code
        == 200
    )


def test_guest_entry_works_beside_production_cognito_with_secure_cookie(guest):
    settings = get_settings()
    settings.environment = "production"
    settings.auth_mode = "cognito"
    settings.app_url = "https://orders.example"
    settings.cognito_region = "us-east-1"
    settings.cognito_user_pool_id = "us-east-1_Example"
    settings.cognito_client_id = "test-client"
    settings.cognito_domain = "https://example.auth.us-east-1.amazoncognito.com"
    guest.base_url = settings.app_url
    response = guest.post("/api/auth/demo", headers={"Origin": settings.app_url})
    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    guest.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    assert guest.get("/api/auth/me").json()["auth_method"] == "demo"
    configuration = guest.get("/api/auth/config").json()
    assert configuration["login_url"] == "/api/auth/login"
    assert configuration["development_login_enabled"] is False
    assert guest.post("/api/auth/logout").json()["logout_url"] is None


def test_rolled_back_guest_claim_restores_all_allowances(guest):
    get_settings().agent_mode = "bedrock"
    session = start_demo(guest)
    workspace_id = session["workspaces"][0]["id"]
    base, _ = first_order(guest, session["workspaces"][0])
    queued = guest.post(base + "/messages", json={"body": "Please change the quantity to 36."})
    assert queued.status_code == 202
    job_id = queued.json()["job"]["id"]
    with session_factory()() as db:
        assert jobs.claim_next(db)[0] == job_id
        db.rollback()
    with session_factory()() as db:
        assert db.get(Workspace, workspace_id).demo_agent_attempts == 0
        assert db.get(DemoDailyUsage, utcnow().date()).bedrock_attempts == 0
        assert db.scalar(select(func.count()).select_from(AgentDailyUsage)) == 0
        assert db.get(Job, job_id).status == "queued"
