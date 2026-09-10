"""Run with `uv run python -m ordertowork.worker` alongside the API."""

import argparse
import asyncio
import logging
from datetime import UTC

from sqlalchemy import select

from ordertowork.config import get_settings
from ordertowork.db import session_factory, utcnow
from ordertowork.models.jobs import Job
from ordertowork.services.agent import interpret_with_strands, reference_interpretation
from ordertowork.services.jobs import claim_next

logger = logging.getLogger("ordertowork.worker")


def current_lease(job: Job, token: str) -> bool:
    deadline = job.leased_until
    if deadline is not None and deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return (
        job.status == "running"
        and job.lease_token == token
        and deadline is not None
        and deadline > utcnow()
    )


async def process_one() -> bool:
    from ordertowork.models.domain import Order, OrderRevision, SourceMessage
    from ordertowork.services.orders import analyze_change, order_snapshot, preview_change

    with session_factory()() as db:
        claimed = claim_next(db)
        db.commit()
    if not claimed:
        return False
    job_id, token = claimed
    events: list[dict] = []
    try:
        with session_factory()() as db:
            job = db.get(Job, job_id)
            message = db.scalar(
                select(SourceMessage).where(
                    SourceMessage.id == job.message_id,
                    SourceMessage.workspace_id == job.workspace_id,
                    SourceMessage.order_id == job.order_id,
                )
            )
            if not message:
                raise RuntimeError("Source message unavailable")
            workspace_id, order_id, body, mode = (
                job.workspace_id,
                job.order_id,
                message.body,
                job.mode,
            )
            order = db.get(Order, order_id)
            expected_revision = order.accepted_revision_id
            snapshot = order_snapshot(db, workspace_id, order_id)
            expected_head = max((r["number"] for r in snapshot["revisions"]), default=0)
        if mode == "reference":
            review = reference_interpretation(snapshot, body)
            events.append(
                {
                    "tool": "reference_interpreter",
                    "input": {"message_id": job.message_id},
                    "result": {"intent": review.intent, "mode": "deterministic; no AI model"},
                }
            )
            if review.intent == "change_request" and not review.missing_fields:
                with session_factory()() as db:
                    preview = preview_change(
                        db, workspace_id, order_id, review.model_dump(exclude_none=True)
                    )
                events.append(
                    {
                        "tool": "preview_change",
                        "input": review.model_dump(exclude_none=True),
                        "result": preview,
                    }
                )
        elif mode == "bedrock":
            review = await asyncio.wait_for(
                interpret_with_strands(workspace_id, order_id, body, events),
                timeout=get_settings().agent_timeout_seconds,
            )
        else:
            raise ValueError("Unknown agent mode")
        with session_factory()() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if not current_lease(job, token):
                return True
            order = db.scalar(
                select(Order)
                .where(Order.id == order_id, Order.workspace_id == workspace_id)
                .with_for_update()
            )
            current_head = (
                db.scalar(
                    select(OrderRevision.number)
                    .where(
                        OrderRevision.order_id == order_id,
                        OrderRevision.workspace_id == workspace_id,
                    )
                    .order_by(OrderRevision.number.desc())
                    .limit(1)
                )
                or 0
            )
            if order.accepted_revision_id != expected_revision or current_head != expected_head:
                raise RuntimeError("Order changed during analysis; retry against current terms")
            analyze_change(db, workspace_id, order_id, review.model_dump(exclude_none=True))
            job.status, job.updated_at = "succeeded", utcnow()
            job.lease_token, job.leased_until = None, None
            job.result = {"intent": review.intent, "missing_fields": review.missing_fields}
            job.tool_events = events
            db.commit()
        logger.info("Analysis completed job=%s mode=%s", job_id, mode)
    except Exception as exc:
        # Do not persist SDK error text: it can include credentials, request content or tokens.
        error = (
            "Bedrock is not configured. Select an accessible model after authenticating AWS."
            if isinstance(exc, ValueError) and "Bedrock model" in str(exc)
            else "Analysis did not complete. Review the request and configuration, then retry."
        )
        with session_factory()() as db:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job and current_lease(job, token):
                job.status, job.error, job.updated_at = "failed", error, utcnow()
                job.lease_token, job.leased_until = None, None
                job.tool_events = events
                db.commit()
        logger.warning("Analysis failed job=%s error_type=%s", job_id, type(exc).__name__)
    return True


async def run(once: bool = False):
    while True:
        processed = await process_one()
        if once:
            return
        if not processed:
            await asyncio.sleep(get_settings().worker_poll_seconds)


def main():
    parser = argparse.ArgumentParser(description="OrderToWork durable analysis worker")
    parser.add_argument("--once", action="store_true", help="Process at most one due job")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run(args.once))


if __name__ == "__main__":
    main()
