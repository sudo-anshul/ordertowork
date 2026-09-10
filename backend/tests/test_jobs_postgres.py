"""PostgreSQL scheduler regressions: lock avoidance and independent work fairness."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from ordertowork.db import Base, utcnow
from ordertowork.models.core import Workspace
from ordertowork.models.domain import Order, SourceMessage
from ordertowork.models.jobs import Job
from ordertowork.services.jobs import claim_next
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session


@pytest.fixture
def scheduler_pg_engine():
    database_url = os.environ.get("OTW_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("OTW_TEST_DATABASE_URL is not configured")
    assert database_url.startswith("postgresql")
    schema = f"otw_scheduler_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def add_order(db, workspace, number):
    order = Order(workspace_id=workspace.id, number=number, customer_name="Scheduler test customer")
    db.add(order)
    db.flush()
    return order


def add_job(db, order, *, running=False):
    message = SourceMessage(
        workspace_id=order.workspace_id, order_id=order.id, body="Scheduler fixture"
    )
    db.add(message)
    db.flush()
    job = Job(
        workspace_id=order.workspace_id,
        order_id=order.id,
        message_id=message.id,
        mode="reference",
        status="running" if running else "queued",
        attempts=1 if running else 0,
        lease_token=uuid4().hex if running else None,
        leased_until=utcnow() + timedelta(minutes=5) if running else None,
    )
    db.add(job)
    db.flush()
    return job


@pytest.mark.postgres
def test_claim_skips_order_locked_by_an_owner_transaction(scheduler_pg_engine):
    engine = scheduler_pg_engine
    with Session(engine, expire_on_commit=False) as db:
        workspace = Workspace(name="Scheduler isolation", profile="bakery")
        db.add(workspace)
        db.flush()
        locked_order = add_order(db, workspace, "LOCKED")
        add_job(db, locked_order)
        independent_order = add_order(db, workspace, "INDEPENDENT")
        independent_job = add_job(db, independent_order)
        db.commit()
        locked_order_id, expected_job_id = locked_order.id, independent_job.id
    with Session(engine) as owner_transaction:
        owner_transaction.scalar(select(Order).where(Order.id == locked_order_id).with_for_update())

        def claim():
            with Session(engine) as worker:
                # Regressions fail in bounded time instead of hanging the suite.
                worker.execute(text("SET LOCAL lock_timeout = '750ms'"))
                result = claim_next(worker)
                worker.commit()
                return result

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(claim)
            try:
                result = future.result(timeout=3)
            finally:
                owner_transaction.rollback()
        assert result is not None
        assert result[0] == expected_job_id


@pytest.mark.postgres
def test_many_busy_orders_do_not_hide_unrelated_pending_work(scheduler_pg_engine):
    engine = scheduler_pg_engine
    with Session(engine, expire_on_commit=False) as db:
        workspace = Workspace(name="Scheduler fairness", profile="bakery")
        db.add(workspace)
        db.flush()
        # More busy candidates than claim_next's bounded candidate batch.
        for index in range(25):
            order = add_order(db, workspace, f"BUSY-{index}")
            add_job(db, order, running=True)
            add_job(db, order)
        independent_order = add_order(db, workspace, "READY")
        independent_job = add_job(db, independent_order)
        db.commit()
        result = claim_next(db)
        db.commit()
        assert result is not None
        assert result[0] == independent_job.id


@pytest.mark.postgres
def test_parallel_claimers_never_run_two_jobs_for_one_order(scheduler_pg_engine):
    engine = scheduler_pg_engine
    with Session(engine, expire_on_commit=False) as db:
        workspace = Workspace(name="Scheduler mutual exclusion", profile="bakery")
        db.add(workspace)
        db.flush()
        order = add_order(db, workspace, "SERIAL")
        add_job(db, order)
        add_job(db, order)
        db.commit()
        order_id = order.id
    barrier = Barrier(2)

    def claim():
        with Session(engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '750ms'"))
            barrier.wait(timeout=3)
            result = claim_next(db)
            db.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sum(result is not None for result in results) == 1
    with Session(engine) as db:
        jobs = list(db.scalars(select(Job).where(Job.order_id == order_id)))
        assert sorted(job.status for job in jobs) == ["queued", "running"]
