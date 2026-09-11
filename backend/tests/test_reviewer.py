"""Protected review sessions must not inherit public quotas or cross tenant boundaries."""

import asyncio
from datetime import datetime, timedelta

import pytest
from ordertowork.config import Settings, get_settings
from ordertowork.db import session_factory, utcnow
from ordertowork.models.auth import AuthSession, DemoDailyUsage
from ordertowork.models.core import User, Workspace
from ordertowork.models.domain import OrderRevision, SourceMessage
from ordertowork.models.jobs import AgentDailyUsage, Job
from ordertowork.services import auth, jobs
from ordertowork.worker import process_one
from sqlalchemy import func, select

TOKEN = "synthetic-reviewer-capability-for-tests-only-123456789"


@pytest.fixture
def reviewer(integrated):
    client = integrated.client
    assert client.post("/api/auth/logout").status_code == 200
    client.headers.pop("X-CSRF-Token", None)
    settings = get_settings()
    settings.reviewer_token_hash = auth.digest(TOKEN)
    settings.reviewer_expires_at = utcnow() + timedelta(days=45)
    settings.demo_enabled = False
    return client


def enter(client):
    response = client.post("/api/auth/reviewer", json={"token": TOKEN})
    assert response.status_code == 200, response.text
    session = response.json()
    client.headers["X-CSRF-Token"] = session["csrf_token"]
    return session


def sample(client, session):
    workspace = next(w for w in session["workspaces"] if w["profile"] == "merchandise")
    prefix = f"/api/workspaces/{workspace['id']}"
    order = client.get(prefix + "/orders").json()["orders"][0]
    path = prefix + "/orders/" + order["id"]
    return workspace, path, client.get(path).json()


def test_review_entry_is_private_reusable_and_independent_of_public_admission(reviewer):
    settings = get_settings()
    settings.demo_enabled = True
    settings.max_daily_demo_sessions = 0
    assert reviewer.post("/api/auth/demo").status_code == 429
    settings.demo_enabled = False
    session = enter(reviewer)
    assert session["auth_method"] == "reviewer" and session["demo"] is None
    assert auth.aware(
        datetime.fromisoformat(session["reviewer"]["expires_at"])
    ) > utcnow() + timedelta(days=40)
    assert len(session["workspaces"]) == 2
    assert session["user"]["is_platform_admin"] is False
    assert session["user"]["email_verified"] is False
    assert TOKEN not in str(session) and settings.reviewer_token_hash not in str(session)
    assert reviewer.get("/api/auth/config").json().get("reviewer_token_hash") is None
    assert enter(reviewer) == session
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        assert db.scalar(select(func.count()).select_from(DemoDailyUsage)) == 0
        user = db.get(User, session["user"]["id"])
        settings.platform_admin_subjects = user.provider_subject
        assert auth.platform_admin(user) is False
    assert reviewer.get("/api/platform/overview").status_code == 403
    assert reviewer.post("/api/auth/logout").json()["logout_url"] is None


def test_invalid_cross_site_and_extra_identity_input_never_issue_review_access(reviewer):
    assert reviewer.post("/api/auth/reviewer", json={"token": "x" * 48}).status_code == 403
    assert (
        reviewer.post(
            "/api/auth/reviewer", json={"token": TOKEN}, headers={"Origin": "https://other.example"}
        ).status_code
        == 403
    )
    assert (
        reviewer.post(
            "/api/auth/reviewer", json={"token": TOKEN, "user_id": "someone-else"}
        ).status_code
        == 422
    )
    get_settings().reviewer_token_hash = ""
    assert reviewer.post("/api/auth/reviewer", json={"token": TOKEN}).status_code == 403
    assert reviewer.get("/api/auth/me").status_code == 401


def test_review_entry_does_not_silently_replace_business_session(integrated):
    client = integrated.client
    settings = get_settings()
    settings.reviewer_token_hash = auth.digest(TOKEN)
    settings.reviewer_expires_at = utcnow() + timedelta(days=45)
    before = client.get("/api/auth/me").json()
    assert client.post("/api/auth/reviewer", json={"token": TOKEN}).status_code == 409
    assert client.get("/api/auth/me").json() == before
    result = client.post(
        "/api/auth/reviewer", json={"token": TOKEN, "replace_business_session": True}
    )
    assert result.status_code == 200 and result.json()["auth_method"] == "reviewer"


def test_reviewers_are_isolated_and_can_start_fresh_without_the_link_token(reviewer):
    first = enter(reviewer)
    first_cookie = reviewer.cookies.get("otw_session")
    reset = reviewer.post("/api/auth/reviewer/reset", headers={"X-CSRF-Token": "wrong"})
    assert reset.status_code == 403
    reset = reviewer.post("/api/auth/reviewer/reset")
    assert reset.status_code == 200
    second = reset.json()
    reviewer.headers["X-CSRF-Token"] = second["csrf_token"]
    assert second["user"]["id"] != first["user"]["id"]
    assert reviewer.get(f"/api/workspaces/{first['workspaces'][0]['id']}").status_code == 404
    with session_factory()() as db:
        old = db.scalar(
            select(AuthSession).where(AuthSession.token_hash == auth.digest(first_cookie))
        )
        assert old.revoked_at is not None
    reviewer.cookies.clear()
    third = enter(reviewer)
    assert third["user"]["id"] != second["user"]["id"]
    assert reviewer.get(f"/api/workspaces/{second['workspaces'][0]['id']}").status_code == 404


def test_review_workspace_creation_preserves_expiry_and_grants_owner_features(reviewer):
    session = enter(reviewer)
    result = reviewer.post(
        "/api/workspaces",
        json={"name": "Reviewer business", "profile": "bakery", "seed_demo": True},
    )
    assert result.status_code == 201
    workspace = result.json()
    assert workspace["is_demo"] is True
    assert workspace["demo_expires_at"] == session["reviewer"]["expires_at"]
    prefix = f"/api/workspaces/{workspace['id']}"
    assert reviewer.patch(prefix, json={"name": "Reviewed business"}).status_code == 200
    resources = reviewer.get(prefix + "/resources").json()
    resource = resources["resources"][0]
    assert (
        reviewer.patch(prefix + f"/resources/{resource['id']}", json={"total": 2000}).status_code
        == 200
    )
    with session_factory()() as db:
        assert (
            db.get(Workspace, workspace["id"]).reviewer_token_hash
            == get_settings().reviewer_token_hash
        )
    get_settings().reviewer_token_hash = "b" * 64
    assert reviewer.get(prefix).status_code == 401


def test_review_analysis_bypasses_public_and_workspace_quotas_but_is_metered(reviewer):
    from unittest.mock import patch

    from ordertowork import worker
    from ordertowork.services.agent import reference_interpretation
    from ordertowork.services.orders import order_snapshot

    settings = get_settings()
    settings.agent_mode = "bedrock"
    settings.max_daily_agent_jobs = 1
    settings.max_demo_agent_jobs = 0
    settings.max_daily_demo_bedrock_attempts = 0
    settings.max_daily_bedrock_attempts = 1
    session = enter(reviewer)
    workspace, path, order = sample(reviewer, session)
    with session_factory()() as db:
        db.add(AgentDailyUsage(day=utcnow().date(), attempts=1))
        db.commit()

    async def no_paid_provider(workspace_id, order_id, body, events, **kwargs):
        with session_factory()() as db:
            return reference_interpretation(order_snapshot(db, workspace_id, order_id), body)

    with patch.object(worker, "interpret_with_strands", no_paid_provider):
        for _ in range(11):
            queued = reviewer.post(path + "/messages", json={"body": order["messages"][0]["body"]})
            assert queued.status_code == 202, queued.text
            assert asyncio.run(process_one()) is True
            with session_factory()() as db:
                assert db.get(Job, queued.json()["job"]["id"]).status == "succeeded"
    with session_factory()() as db:
        usage = db.get(AgentDailyUsage, utcnow().date())
        assert (usage.attempts, usage.reviewer_attempts) == (1, 11)
        assert db.get(Workspace, workspace["id"]).demo_agent_attempts == 0
        assert db.scalar(select(func.count()).select_from(DemoDailyUsage)) == 0
        assert (
            db.scalar(
                select(func.count())
                .select_from(SourceMessage)
                .where(SourceMessage.order_id == order["id"])
            )
            == 12
        )
    settings.max_daily_bedrock_attempts = 0
    queued = reviewer.post(path + "/messages", json={"body": order["messages"][0]["body"]})
    assert queued.status_code == 202
    assert asyncio.run(process_one()) is False
    with session_factory()() as db:
        assert db.get(Job, queued.json()["job"]["id"]).status == "failed"
        assert db.get(AgentDailyUsage, utcnow().date()).reviewer_attempts == 11


@pytest.mark.parametrize("revoke", ["expiry", "rotation"])
def test_review_revocation_blocks_session_customer_links_and_queued_work(reviewer, revoke):
    get_settings().agent_mode = "bedrock"
    session = enter(reviewer)
    workspace, path, detail = sample(reviewer, session)
    option = next(r for r in detail["revisions"] if r["status"] == "proposed" and r["feasible"])
    link = reviewer.post(path + f"/proposals/{option['id']}/share").json()
    token = link["url"].rsplit("/", 1)[1]
    queued = reviewer.post(path + "/messages", json={"body": detail["messages"][0]["body"]})
    assert queued.status_code == 202
    if revoke == "expiry":
        get_settings().reviewer_expires_at = utcnow() - timedelta(seconds=1)
    else:
        get_settings().reviewer_token_hash = "c" * 64
    assert reviewer.get("/api/auth/me").status_code == 401
    assert reviewer.get(f"/api/customer/{token}").status_code == 410
    with session_factory()() as db:
        assert jobs.claim_next(db) is None
        db.commit()
        assert db.get(Job, queued.json()["job"]["id"]).status == "failed"
        assert db.scalar(select(func.count()).select_from(AgentDailyUsage)) == 0


def test_review_revocation_during_model_run_cannot_apply_results(reviewer, monkeypatch):
    from ordertowork import worker
    from ordertowork.services.agent import reference_interpretation
    from ordertowork.services.orders import order_snapshot

    get_settings().agent_mode = "bedrock"
    session = enter(reviewer)
    workspace, path, detail = sample(reviewer, session)
    queued = reviewer.post(path + "/messages", json={"body": detail["messages"][0]["body"]})

    async def expire_access(workspace_id, order_id, body, events, **kwargs):
        with session_factory()() as db:
            result = reference_interpretation(order_snapshot(db, workspace_id, order_id), body)
        get_settings().reviewer_expires_at = utcnow() - timedelta(seconds=1)
        return result

    monkeypatch.setattr(worker, "interpret_with_strands", expire_access)
    assert asyncio.run(process_one()) is True
    with session_factory()() as db:
        assert db.get(Job, queued.json()["job"]["id"]).status == "failed"
        assert db.scalar(
            select(func.count())
            .select_from(OrderRevision)
            .where(OrderRevision.order_id == detail["id"])
        ) == len(detail["revisions"])


@pytest.mark.parametrize(
    "values",
    [
        {"reviewer_token_hash": "a" * 64},
        {"reviewer_expires_at": "2026-10-16T00:00:00Z"},
        {"reviewer_token_hash": "a" * 64, "reviewer_expires_at": "2026-10-16T00:00:00"},
        {"reviewer_token_hash": TOKEN, "reviewer_expires_at": "2026-10-16T00:00:00Z"},
    ],
)
def test_review_settings_reject_partial_naive_or_raw_secret_configuration(values):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **values)
