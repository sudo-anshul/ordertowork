from fastapi import APIRouter, Depends, HTTPException
from ordertowork.config import get_settings
from ordertowork.db import get_db, utcnow
from ordertowork.models.core import Workspace
from ordertowork.models.jobs import Job
from ordertowork.services.auth import Actor, get_actor, require_membership
from ordertowork.services.jobs import job_payload
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/workspaces/{workspace_id}/jobs", tags=["jobs"])


def owned_job(db: Session, workspace_id: str, job_id: str, lock=False) -> Job:
    query = select(Job).where(Job.id == job_id, Job.workspace_id == workspace_id)
    job = db.scalar(query.with_for_update() if lock else query)
    if not job:
        raise HTTPException(404, detail={"code": "not_found", "message": "Analysis not found."})
    return job


@router.get("/{job_id}")
def read_job(
    workspace_id: str, job_id: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)
):
    require_membership(db, actor, workspace_id, roles=("owner",))
    return job_payload(owned_job(db, workspace_id, job_id))


@router.post("/{job_id}/retry")
def retry_job(
    workspace_id: str, job_id: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)
):
    require_membership(db, actor, workspace_id, roles=("owner",))
    job = owned_job(db, workspace_id, job_id, lock=True)
    workspace = db.get(Workspace, workspace_id)
    if (
        workspace.demo_expires_at is not None
        and workspace.demo_agent_attempts >= get_settings().max_demo_agent_jobs
    ):
        raise HTTPException(
            429,
            detail={
                "code": "demo_analysis_limit",
                "message": "This demo has used its analysis allowance. You can still explore the prepared options.",
            },
        )
    if job.status != "failed":
        raise HTTPException(
            409, detail={"code": "not_failed", "message": "Only failed analysis can be retried."}
        )
    # Retry grants one bounded additional attempt; approvals/reservations are never replayed.
    if job.attempts >= 6:
        raise HTTPException(
            429,
            detail={
                "code": "retry_limit",
                "message": "Retry limit reached. Submit a new request after resolving the issue.",
            },
        )
    job.status, job.max_attempts = "queued", job.attempts + 1
    job.lease_token, job.leased_until, job.error = None, None, None
    job.updated_at = utcnow()
    db.commit()
    return job_payload(job)
