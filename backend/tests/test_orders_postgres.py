"""Actual PostgreSQL concurrency test; a SQLite pass cannot replace this test.

Set OTW_TEST_DATABASE_URL to a disposable PostgreSQL database. A unique temporary
schema isolates these tables; the test drops only that schema during cleanup.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from ordertowork.db import Base, utcnow
from ordertowork.models import jobs  # noqa: F401
from ordertowork.models.core import Workspace
from ordertowork.models.domain import Reservation, Resource
from ordertowork.services import orders as service
from ordertowork.services.profiles import configure_workspace
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session


@pytest.mark.postgres
def test_two_customer_approvals_cannot_overbook_same_resources():
    database_url = os.environ.get("OTW_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("OTW_TEST_DATABASE_URL is not configured")
    assert database_url.startswith("postgresql"), "PostgreSQL is required for this concurrency test"
    schema = f"otw_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as db:
            workspace = Workspace(name="Concurrency test bakery", profile="bakery")
            db.add(workspace)
            db.flush()
            product = configure_workspace(db, workspace)
            for resource in db.scalars(
                select(Resource).where(Resource.workspace_id == workspace.id)
            ):
                resource.total = 60 if resource.kind == "capacity" else 50
            tokens = []
            for customer in ("First", "Second"):
                order = service.create_order(
                    db,
                    workspace.id,
                    {
                        "customer_name": customer,
                        "product_id": product.id,
                        "quantity": 20,
                        "variant": "vanilla",
                        "pickup_at": (utcnow() + timedelta(days=4)).isoformat(),
                    },
                )
                revision = service.revisions_for(db, order)[0]
                shared = service.share_proposal(db, workspace.id, order.id, revision.id)
                tokens.append((shared["url"].rsplit("/", 1)[1], revision.terms_hash, order.id))
            db.commit()
        barrier = Barrier(2)

        def approve(args):
            token, digest, order_id = args
            with Session(engine) as db:
                barrier.wait(timeout=10)
                try:
                    result = service.approve_customer(db, token, digest, True)
                    db.commit()
                    return result["status"], order_id
                except HTTPException as exc:
                    db.commit()  # diagnostic failure updates, no accepted resources
                    return exc.detail["code"], order_id

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(approve, tokens))
        assert sorted(status for status, _ in results) == ["approved", "availability_changed"]
        with Session(engine) as db:
            for resource in db.scalars(select(Resource)):
                assert service.reserved(db, resource.id) <= resource.total
            loser_id = next(
                order_id for status, order_id in results if status == "availability_changed"
            )
            assert service.get_order(db, workspace.id, loser_id).accepted_revision_id is None
            assert not db.scalar(
                select(Reservation.id).where(
                    Reservation.order_id == loser_id, Reservation.status == "held"
                )
            )
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()
