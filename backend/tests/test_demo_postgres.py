"""Real PostgreSQL checks for the shared limits on anonymous demo workspaces.

These tests never contact a model provider. Set OTW_TEST_DATABASE_URL to a
disposable PostgreSQL database; each test owns and removes one temporary schema.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from ordertowork.config import get_settings
from ordertowork.db import Base, utcnow
from ordertowork.models.auth import DemoDailyUsage
from ordertowork.models.core import Workspace
from ordertowork.models.domain import Order, SourceMessage
from ordertowork.models.jobs import AgentDailyUsage, Job
from ordertowork.services import demo, jobs
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session


@pytest.fixture
def demo_pg_engine(monkeypatch):
    database_url = os.environ.get("OTW_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("OTW_TEST_DATABASE_URL is not configured")
    assert database_url.startswith("postgresql"), "These checks require actual PostgreSQL"
    schema = f"otw_demo_test_{uuid4().hex}"
    admin = create_engine(database_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
    settings = get_settings()
    for name, value in {
        "demo_enabled": True,
        "max_daily_demo_sessions": 50,
        "max_demo_agent_jobs": 2,
        "max_daily_demo_bedrock_attempts": 20,
        "max_daily_bedrock_attempts": 100,
    }.items():
        monkeypatch.setattr(settings, name, value)
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def add_workspace(db, *, expires_at=None):
    workspace = Workspace(
        name="Synthetic quota check",
        profile="bakery",
        is_demo=expires_at is not None,
        demo_expires_at=expires_at,
    )
    db.add(workspace)
    db.flush()
    return workspace


def add_job(db, workspace, *, max_attempts=1):
    order = Order(
        workspace_id=workspace.id,
        number=uuid4().hex,
        customer_name="Synthetic demo customer",
    )
    db.add(order)
    db.flush()
    message = SourceMessage(
        workspace_id=workspace.id, order_id=order.id, body="Synthetic quota check"
    )
    db.add(message)
    db.flush()
    job = Job(
        workspace_id=workspace.id,
        order_id=order.id,
        message_id=message.id,
        mode="bedrock",
        max_attempts=max_attempts,
    )
    db.add(job)
    db.flush()
    return job


@pytest.mark.postgres
def test_concurrent_demo_admissions_and_paid_attempts_preserve_both_limits(
    demo_pg_engine, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_daily_demo_sessions", 3)
    monkeypatch.setattr(settings, "max_daily_demo_bedrock_attempts", 3)
    barrier = Barrier(12)

    def reserve(kind):
        with Session(demo_pg_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            barrier.wait(timeout=10)
            reserve_slot = (
                demo.reserve_demo_session
                if kind == "session"
                else demo.reserve_demo_bedrock_attempt
            )
            accepted = reserve_slot(db)
            db.commit()
            return kind, accepted

    # Both counters share the same previously absent UTC-day row. Concurrent
    # inserts/upserts must neither exceed one limit nor erase the other counter.
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(reserve, ["session", "paid"] * 6))
    for kind in ("session", "paid"):
        assert sum(accepted for requested, accepted in results if requested == kind) == 3
    with Session(demo_pg_engine) as db:
        usage = db.get(DemoDailyUsage, utcnow().date())
        assert (usage.sessions, usage.bedrock_attempts) == (3, 3)


@pytest.mark.postgres
@pytest.mark.parametrize("exhausted_scope", ["demo", "global"])
def test_denied_paid_claim_does_not_consume_other_budgets(
    demo_pg_engine, monkeypatch, exhausted_scope
):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_daily_demo_bedrock_attempts", 1)
    monkeypatch.setattr(settings, "max_daily_bedrock_attempts", 1)
    with Session(demo_pg_engine, expire_on_commit=False) as db:
        workspace = add_workspace(db, expires_at=utcnow() + timedelta(hours=1))
        blocked_job = add_job(db, workspace)
        db.add(
            DemoDailyUsage(
                day=utcnow().date(), sessions=1, bedrock_attempts=int(exhausted_scope == "demo")
            )
        )
        db.add(AgentDailyUsage(day=utcnow().date(), attempts=int(exhausted_scope == "global")))
        db.commit()
        workspace_id, job_id = workspace.id, blocked_job.id

    with Session(demo_pg_engine) as db:
        assert jobs.claim_next(db) is None
        db.commit()

    with Session(demo_pg_engine) as db:
        assert db.get(Workspace, workspace_id).demo_agent_attempts == 0
        assert db.get(DemoDailyUsage, utcnow().date()).bedrock_attempts == int(
            exhausted_scope == "demo"
        )
        assert db.get(AgentDailyUsage, utcnow().date()).attempts == int(exhausted_scope == "global")
        assert db.get(Job, job_id).status == "failed"
        if exhausted_scope == "demo":
            # Demo traffic cannot spend the capacity reserved for business users.
            business = add_workspace(db)
            business_job = add_job(db, business)
            expected_id = business_job.id
            db.commit()
            claim = jobs.claim_next(db)
            db.commit()
            assert claim is not None and claim[0] == expected_id
            assert db.get(AgentDailyUsage, utcnow().date()).attempts == 1
            assert db.get(DemoDailyUsage, utcnow().date()).bedrock_attempts == 1


@pytest.mark.postgres
def test_lease_recovery_and_utc_rollover_cannot_reset_demo_lifetime_limit(
    demo_pg_engine, monkeypatch
):
    now = utcnow().replace(hour=23, minute=45, second=0, microsecond=0)
    clock = [now]
    monkeypatch.setattr(jobs, "utcnow", lambda: clock[0])
    monkeypatch.setattr(demo, "utcnow", lambda: clock[0])
    with Session(demo_pg_engine, expire_on_commit=False) as db:
        workspace = add_workspace(db, expires_at=now + timedelta(hours=1))
        original = add_job(db, workspace, max_attempts=2)
        db.commit()
        workspace_id, original_id = workspace.id, original.id
        assert jobs.claim_next(db)[0] == original_id
        db.commit()

    clock[0] = now + timedelta(minutes=5)
    with Session(demo_pg_engine) as db:
        original = db.get(Job, original_id)
        original.leased_until = clock[0] - timedelta(seconds=1)
        db.commit()
        # Worker recovery is another paid attempt, even for the same message.
        assert jobs.claim_next(db)[0] == original_id
        db.commit()
        original.status = "succeeded"
        original.lease_token = original.leased_until = None
        fresh = add_job(db, db.get(Workspace, workspace_id))
        fresh_id = fresh.id
        db.commit()

    # The one-hour demo crosses UTC midnight. Its lifetime allowance does not.
    clock[0] = now + timedelta(minutes=30)
    with Session(demo_pg_engine) as db:
        assert jobs.claim_next(db) is None
        db.commit()
        assert db.get(Workspace, workspace_id).demo_agent_attempts == 2
        assert db.get(Job, original_id).attempts == 2
        assert db.get(Job, fresh_id).status == "failed"
        assert db.get(AgentDailyUsage, now.date()).attempts == 2
        assert db.get(DemoDailyUsage, now.date()).bedrock_attempts == 2
        assert db.get(AgentDailyUsage, clock[0].date()) is None
        assert db.get(DemoDailyUsage, clock[0].date()) is None
        assert len(list(db.scalars(select(Job)))) == 2


@pytest.mark.postgres
def test_concurrent_reviewer_metering_does_not_compete_with_public_budget(
    demo_pg_engine, monkeypatch
):
    monkeypatch.setattr(get_settings(), "max_daily_bedrock_attempts", 2)
    barrier = Barrier(10)

    def reserve(reviewer):
        with Session(demo_pg_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            barrier.wait(timeout=10)
            accepted = jobs.reserve_bedrock_attempt(db, reviewer=reviewer)
            db.commit()
            return reviewer, accepted

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(reserve, [True, False] * 5))
    assert sum(ok for reviewer, ok in results if reviewer) == 5
    assert sum(ok for reviewer, ok in results if not reviewer) == 2
    with Session(demo_pg_engine) as db:
        usage = db.get(AgentDailyUsage, utcnow().date())
        assert (usage.attempts, usage.reviewer_attempts) == (2, 5)
        assert jobs.reserve_bedrock_attempt(db, reviewer=True)
        db.rollback()
        db.expire_all()
        assert db.get(AgentDailyUsage, utcnow().date()).reviewer_attempts == 5


@pytest.mark.postgres
def test_reviewer_job_precedes_public_backlog_and_keeps_lease_serialization(
    demo_pg_engine, monkeypatch
):
    settings = get_settings()
    key = "a" * 64
    deadline = utcnow() + timedelta(days=45)
    monkeypatch.setattr(settings, "reviewer_token_hash", key)
    monkeypatch.setattr(settings, "reviewer_expires_at", deadline)
    with Session(demo_pg_engine, expire_on_commit=False) as db:
        public = add_workspace(db, expires_at=utcnow() + timedelta(hours=1))
        public_job = add_job(db, public)
        reviewer = add_workspace(db, expires_at=deadline)
        reviewer.reviewer_token_hash = key
        reviewer_job = add_job(db, reviewer)
        public_id, reviewer_id = public_job.id, reviewer_job.id
        db.commit()
        assert jobs.claim_next(db)[0] == reviewer_id
        db.commit()
        assert db.get(Job, public_id).status == "queued"
        assert db.get(Job, reviewer_id).attempts == 1
        assert jobs.claim_next(db)[0] == public_id
        db.commit()
        usage = db.get(AgentDailyUsage, utcnow().date())
        assert (usage.attempts, usage.reviewer_attempts) == (1, 1)
