"""Temporary guest lifecycle and shared, transactional demo budget reservations."""

import secrets
from datetime import UTC

from ordertowork.config import get_settings
from ordertowork.db import utcnow
from ordertowork.models.auth import DemoDailyUsage
from ordertowork.models.core import Workspace
from sqlalchemy.orm import Session


def reviewer_access_active(token_hash: str | None) -> bool:
    settings = get_settings()
    return bool(
        token_hash
        and settings.reviewer_token_hash
        and secrets.compare_digest(token_hash, settings.reviewer_token_hash)
        and settings.reviewer_expires_at
        and settings.reviewer_expires_at > utcnow()
    )


def public_demo_workspace(workspace: Workspace) -> bool:
    return workspace.demo_expires_at is not None and workspace.reviewer_token_hash is None


def demo_workspace_active(workspace: Workspace) -> bool:
    if workspace.reviewer_token_hash and (
        workspace.demo_expires_at is None
        or not reviewer_access_active(workspace.reviewer_token_hash)
    ):
        return False
    if workspace.demo_expires_at is None:
        return True
    deadline = workspace.demo_expires_at
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    enabled = bool(workspace.reviewer_token_hash) or get_settings().demo_enabled
    return enabled and deadline > utcnow()


def _reserve_daily(db: Session, column: str, limit: int) -> bool:
    if limit <= 0:
        return False
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert

    counter = getattr(DemoDailyUsage, column)
    statement = insert(DemoDailyUsage).values(
        day=utcnow().date(),
        sessions=int(column == "sessions"),
        bedrock_attempts=int(column == "bedrock_attempts"),
    )
    statement = statement.on_conflict_do_update(
        index_elements=[DemoDailyUsage.day],
        set_={column: counter + 1},
        where=counter < limit,
    ).returning(counter)
    return db.scalar(statement) is not None


def reserve_demo_session(db: Session) -> bool:
    return _reserve_daily(db, "sessions", get_settings().max_daily_demo_sessions)


def reserve_demo_bedrock_attempt(db: Session) -> bool:
    return _reserve_daily(db, "bedrock_attempts", get_settings().max_daily_demo_bedrock_attempts)
