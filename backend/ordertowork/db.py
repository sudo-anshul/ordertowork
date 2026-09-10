from collections.abc import Generator
from datetime import UTC, datetime
from functools import lru_cache
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ordertowork.config import get_settings


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


@lru_cache
def get_engine():
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def session_factory() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with session_factory()() as session:
        yield session
