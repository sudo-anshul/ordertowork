"""Paid runs have a shared budget, including lease recovery and owner retries."""

from datetime import timedelta

from ordertowork.config import get_settings
from ordertowork.db import session_factory, utcnow
from ordertowork.models.jobs import AgentDailyUsage, Job
from ordertowork.services import jobs


def test_daily_budget_resets_by_utc_day_and_zero_disables(integrated, monkeypatch):
    settings = get_settings()
    settings.max_daily_bedrock_attempts = 2
    today = utcnow()
    with session_factory()() as db:
        assert jobs.reserve_bedrock_attempt(db)
        assert jobs.reserve_bedrock_attempt(db)
        assert not jobs.reserve_bedrock_attempt(db)
        db.commit()
        assert db.get(AgentDailyUsage, today.date()).attempts == 2
        monkeypatch.setattr(jobs, "utcnow", lambda: today + timedelta(days=1))
        assert jobs.reserve_bedrock_attempt(db)
        settings.max_daily_bedrock_attempts = 0
        assert not jobs.reserve_bedrock_attempt(db)


def test_expired_lease_consumes_budget_and_new_workspace_cannot_reset_it(integrated):
    settings = get_settings()
    settings.agent_mode = "bedrock"
    settings.max_daily_bedrock_attempts = 2
    client = integrated.client
    identifiers = []
    for name in ("First business", "Second business"):
        workspace = client.post(
            "/api/workspaces", json={"name": name, "profile": "bakery", "seed_demo": True}
        ).json()
        prefix = f"/api/workspaces/{workspace['id']}"
        order = client.get(prefix + "/orders").json()["orders"][0]
        response = client.post(
            prefix + f"/orders/{order['id']}/messages", json={"body": "Please make 36 cupcakes."}
        )
        assert response.status_code == 202
        identifiers.append(response.json()["job"]["id"])
    with session_factory()() as db:
        first = jobs.claim_next(db)
        db.commit()
        assert first[0] == identifiers[0]
        db.get(Job, identifiers[0]).leased_until = utcnow() - timedelta(seconds=1)
        db.commit()
        recovered = jobs.claim_next(db)
        db.commit()
        assert recovered[0] == identifiers[0]
        assert recovered[1] != first[1]
        assert jobs.claim_next(db) is None
        db.commit()
        blocked = db.get(Job, identifiers[1])
        assert blocked.status == "failed"
        assert "budget reached" in blocked.error
        assert blocked.attempts == 0
        assert db.get(AgentDailyUsage, utcnow().date()).attempts == 2


def test_rolled_back_claim_does_not_consume_budget(integrated):
    get_settings().max_daily_bedrock_attempts = 1
    with session_factory()() as db:
        assert jobs.reserve_bedrock_attempt(db)
        db.rollback()
        assert jobs.reserve_bedrock_attempt(db)
        db.commit()
        assert not jobs.reserve_bedrock_attempt(db)
