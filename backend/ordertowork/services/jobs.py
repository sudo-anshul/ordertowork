"""Durable, bounded job scheduling. Business effects commit with the job result."""

from datetime import timedelta

from fastapi import HTTPException
from ordertowork.config import get_settings
from ordertowork.db import new_id, utcnow
from ordertowork.models.core import Workspace
from ordertowork.models.jobs import Job
from sqlalchemy import func, or_, select
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
    existing = db.scalar(
        select(Job).where(Job.workspace_id == workspace_id, Job.message_id == message_id)
    )
    if existing:
        return existing
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
    if daily_count >= settings.max_daily_agent_jobs:
        raise HTTPException(
            429, detail={"code": "usage_limit", "message": "Daily analysis limit reached."}
        )
    job = Job(
        workspace_id=workspace_id,
        order_id=order_id,
        message_id=message_id,
        mode=settings.agent_mode,
        max_attempts=settings.max_job_attempts,
    )
    db.add(job)
    db.flush()
    return job


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
        .where(
            Job.attempts < Job.max_attempts,
            ~busy_order,
            or_(Job.status == "queued", (Job.status == "running") & (Job.leased_until < now)),
        )
        .order_by(Job.created_at)
        .limit(20)
        .with_for_update(skip_locked=True)
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
        token = new_id()
        job.status, job.lease_token = "running", token
        job.leased_until = now + timedelta(seconds=get_settings().worker_lease_seconds)
        job.attempts += 1
        job.error, job.updated_at = None, now
        db.flush()
        return job.id, token
    return None
