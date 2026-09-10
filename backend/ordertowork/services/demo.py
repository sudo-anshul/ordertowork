"""Temporary guest lifecycle and shared, transactional demo budget reservations."""

from datetime import UTC

from ordertowork.config import get_settings
from ordertowork.db import utcnow
from ordertowork.models.auth import DemoDailyUsage
from ordertowork.models.core import Workspace
from sqlalchemy.orm import Session


def demo_workspace_active(workspace: Workspace) -> bool:
    if workspace.demo_expires_at is None:
        return True
    deadline = workspace.demo_expires_at
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return get_settings().demo_enabled and deadline > utcnow()


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
