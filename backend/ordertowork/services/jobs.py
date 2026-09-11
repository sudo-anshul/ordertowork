"""Durable, bounded job scheduling. Business effects commit with the job result."""

from datetime import timedelta

from fastapi import HTTPException
from ordertowork.config import get_settings
from ordertowork.db import new_id, utcnow
from ordertowork.models.core import Workspace
from ordertowork.models.jobs import AgentDailyUsage, Job
from ordertowork.services.demo import (
    demo_workspace_active,
    public_demo_workspace,
    reserve_demo_bedrock_attempt,
    reviewer_access_active,
)
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.orm import Session, aliased


def job_payload(job: Job) -> dict:
    return {
        name: getattr(job, name)
        for name in (
            "id",
            "order_id",
            "message_id",
            "status",
            "attempts",
            "error",
            "mode",
            "tool_events",
            "created_at",
            "updated_at",
        )
    }


def enqueue_analysis(db: Session, workspace_id: str, order_id: str, message_id: str) -> Job:
    settings = get_settings()
    workspace = db.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update())
    if not workspace or workspace.status != "active":
        raise HTTPException(404, detail={"code": "not_found", "message": "Workspace not found."})
    if not demo_workspace_active(workspace):
        raise HTTPException(410, detail={"code": "demo_expired", "message": "This demo has ended."})
    existing = db.scalar(
        select(Job).where(Job.workspace_id == workspace_id, Job.message_id == message_id)
    )
    if existing:
        return existing
    if public_demo_workspace(workspace):
        lifetime_jobs = (
            db.scalar(select(func.count()).select_from(Job).where(Job.workspace_id == workspace_id))
            or 0
        )
        if (
            lifetime_jobs >= settings.max_demo_agent_jobs
            or workspace.demo_agent_attempts >= settings.max_demo_agent_jobs
        ):
            raise HTTPException(
                429,
                detail={
                    "code": "demo_analysis_limit",
                    "message": "This demo has used its analysis allowance. You can still explore the prepared options.",
                },
            )
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    daily_count = (
        db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.workspace_id == workspace_id,
                Job.created_at >= day_start,
            )
        )
        or 0
    )
    if not workspace.reviewer_token_hash and daily_count >= settings.max_daily_agent_jobs:
        raise HTTPException(
            429, detail={"code": "usage_limit", "message": "Daily analysis limit reached."}
        )
    job = Job(
        workspace_id=workspace_id,
        order_id=order_id,
        message_id=message_id,
        mode=settings.agent_mode,
        max_attempts=1 if workspace.demo_expires_at is not None else settings.max_job_attempts,
    )
    db.add(job)
    db.flush()
    return job


def reserve_bedrock_attempt(db: Session, *, reviewer: bool = False) -> bool:
    """Atomically reserve one paid run before invoking a provider, across all tenants.

    Retried/expired jobs reserve again. Failed runs are not refunded because the
    provider may already have processed tokens. A zero limit is the kill switch.
    The reservation commits in the same transaction as the worker lease.
    """
    limit = get_settings().max_daily_bedrock_attempts
    if limit == 0:
        return False
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert

    counter = AgentDailyUsage.reviewer_attempts if reviewer else AgentDailyUsage.attempts
    statement = insert(AgentDailyUsage).values(
        day=utcnow().date(), attempts=int(not reviewer), reviewer_attempts=int(reviewer)
    )
    statement = statement.on_conflict_do_update(
        index_elements=[AgentDailyUsage.day],
        set_={counter.key: counter + 1},
        where=True if reviewer else counter < limit,
    ).returning(counter)
    return db.scalar(statement) is not None


def reserve_job_attempt(db: Session, workspace: Workspace, job: Job) -> str | None:
    """Reserve all applicable limits together; a partial reservation never consumes allowance."""
    settings = get_settings()
    if workspace.reviewer_token_hash and not demo_workspace_active(workspace):
        return "This reviewer access has ended."
    if workspace.demo_expires_at is None and job.mode != "bedrock":
        return None
    if db.get_bind().dialect.name == "sqlite":
        # sqlite3 legacy transaction mode does not begin a transaction for a
        # SELECT or SAVEPOINT. Ensure releasing this savepoint cannot commit a
        # reservation before the surrounding worker lease transaction commits.
        connection = db.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
    with db.begin_nested() as reservation:
        if public_demo_workspace(workspace):
            claimed = db.execute(
                update(Workspace)
                .where(
                    Workspace.id == workspace.id,
                    Workspace.demo_expires_at > utcnow(),
                    Workspace.demo_agent_attempts < settings.max_demo_agent_jobs,
                )
                .values(demo_agent_attempts=Workspace.demo_agent_attempts + 1)
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                reservation.rollback()
                return "This demo has used its analysis allowance. Explore the prepared options."
            if job.mode == "bedrock" and not reserve_demo_bedrock_attempt(db):
                reservation.rollback()
                return "Today's live demo AI allowance is used. Explore the prepared options or try after 00:00 UTC."
        if job.mode == "bedrock" and not reserve_bedrock_attempt(
            db, reviewer=reviewer_access_active(workspace.reviewer_token_hash)
        ):
            reservation.rollback()
            return "Daily live-AI budget reached. Retry after 00:00 UTC or contact the owner."
    if public_demo_workspace(workspace):
        db.expire(workspace, ["demo_agent_attempts"])
    return None


def claim_next(db: Session) -> tuple[str, str] | None:
    from ordertowork.models.domain import Order

    now = utcnow()
    # Mark exhausted leases terminal instead of leaving them permanently running.
    exhausted = db.scalars(
        select(Job)
        .where(
            Job.status == "running",
            Job.leased_until < now,
            Job.attempts >= Job.max_attempts,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for job in exhausted:
        job.status, job.error, job.updated_at = (
            "failed",
            "Worker stopped before completion. Retry the analysis.",
            now,
        )
        job.lease_token, job.leased_until = None, None
    other = aliased(Job)
    busy_order = (
        select(other.id)
        .where(
            other.order_id == Job.order_id,
            other.id != Job.id,
            other.status == "running",
            other.leased_until > now,
        )
        .exists()
    )
    candidates = db.scalars(
        select(Job)
        .join(Workspace, Workspace.id == Job.workspace_id)
        .where(
            Job.attempts < Job.max_attempts,
            ~busy_order,
            or_(Job.status == "queued", (Job.status == "running") & (Job.leased_until < now)),
        )
        .order_by(case((Workspace.reviewer_token_hash.is_not(None), 0), else_=1), Job.created_at)
        .limit(20)
        .with_for_update(skip_locked=True, of=Job)
    ).all()
    for job in candidates:
        # The same order is analyzed serially, including across worker processes.
        locked_order = db.scalar(
            select(Order).where(Order.id == job.order_id).with_for_update(skip_locked=True)
        )
        if locked_order is None:
            continue
        workspace = db.get(Workspace, job.workspace_id)
        if not workspace or workspace.status != "active":
            job.status, job.error, job.updated_at = "failed", "Workspace is not active.", now
            continue
        if not demo_workspace_active(workspace):
            job.status, job.error, job.updated_at = "failed", "This demo has ended.", now
            job.lease_token, job.leased_until = None, None
            continue
        busy = db.scalar(
            select(Job.id)
            .where(
                Job.order_id == job.order_id,
                Job.id != job.id,
                Job.status == "running",
                Job.leased_until > now,
            )
            .limit(1)
        )
        if busy:
            continue
        budget_error = reserve_job_attempt(db, workspace, job)
        if budget_error:
            job.status, job.error, job.updated_at = (
                "failed",
                budget_error,
                now,
            )
            job.lease_token, job.leased_until = None, None
            continue
        token = new_id()
        job.status, job.lease_token = "running", token
        job.leased_until = now + timedelta(seconds=get_settings().worker_lease_seconds)
        job.attempts += 1
        job.error, job.updated_at = None, now
        db.flush()
        return job.id, token
    return None
