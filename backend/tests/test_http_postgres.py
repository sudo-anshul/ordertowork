"""Complete HTTP→worker→customer→production workflow on isolated PostgreSQL tables."""

import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from ordertowork.config import get_settings
from ordertowork.db import Base, get_engine, session_factory
from ordertowork.main import create_app
from ordertowork.models.domain import Order
from ordertowork.worker import process_one
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@pytest.fixture
def pg_http_client(monkeypatch, tmp_path):
    database_url = os.environ.get("OTW_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("OTW_TEST_DATABASE_URL is not configured")
    assert database_url.startswith("postgresql")
    schema = f"otw_http_test_{uuid4().hex}"
    admin = create_engine(database_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated_url = make_url(database_url).update_query_dict({"options": f"-csearch_path={schema}"})
    for key, value in {
        "OTW_DATABASE_URL": isolated_url.render_as_string(hide_password=False),
        "OTW_ENVIRONMENT": "development",
        "OTW_AUTH_MODE": "development",
        "OTW_APP_URL": "http://localhost:5173",
        "OTW_AGENT_MODE": "reference",
        "OTW_STORAGE_MODE": "local",
        "OTW_DATA_DIR": str(tmp_path / "data"),
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    get_engine.cache_clear()
    try:
        Base.metadata.create_all(get_engine())
        with TestClient(
            create_app(), base_url="http://localhost:5173", client=("127.0.0.1", 49000)
        ) as client:
            login = client.post(
                "/api/auth/development-login",
                json={"email": "pg-owner@example.test", "name": "PostgreSQL owner"},
            )
            assert login.status_code == 200, login.text
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            yield client
    finally:
        get_engine().dispose()
        get_engine.cache_clear()
        get_settings.cache_clear()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.mark.postgres
@pytest.mark.parametrize(
    "profile,total,top_up", [("merchandise", 81000, 13500), ("bakery", 14400, 2400)]
)
def test_postgres_http_order_workflow(pg_http_client, profile, total, top_up):
    client = pg_http_client
    created = client.post(
        "/api/workspaces",
        json={"name": "PG sample " + profile, "profile": profile, "seed_demo": True},
    )
    assert created.status_code == 201, created.text
    wid = created.json()["id"]
    summaries = client.get(f"/api/workspaces/{wid}/orders").json()["orders"]
    oid = summaries[0]["id"]
    path = f"/api/workspaces/{wid}/orders/{oid}"
    initial = client.get(path).json()
    queued = client.post(path + "/messages", json={"body": initial["messages"][-1]["body"]})
    assert queued.status_code == 202, queued.text
    assert asyncio.run(process_one())
    job = client.get(f"/api/workspaces/{wid}/jobs/{queued.json()['job']['id']}").json()
    assert job["status"] == "succeeded", job
    assert job["mode"] == "reference"
    detail = client.get(path).json()
    option = next(
        r
        for r in detail["revisions"]
        if r["status"] == "proposed" and r["feasible"] and r["terms"]["total_cents"] == total
    )
    shared = client.post(path + f"/proposals/{option['id']}/share", json={})
    assert shared.status_code == 200, shared.text
    token = shared.json()["url"].rsplit("/", 1)[1]
    assert client.get(f"/api/customer/{token}").json()["top_up_cents"] == top_up
    assert (
        client.post(
            f"/api/customer/{token}/approve",
            json={"terms_hash": option["terms_hash"], "consent": True},
        ).status_code
        == 200
    )
    assert client.get(path + "/ticket").status_code == 409
    payment = {
        "amount_cents": top_up,
        "reference": "Synthetic externally recorded payment",
        "idempotency_key": "pg-fixture-payment",
    }
    assert client.post(path + "/deposits", json=payment).status_code == 200
    assert client.post(path + "/deposits", json=payment).status_code == 200
    ticket = client.get(path + "/ticket").json()
    assert ticket["terms"]["total_cents"] == total
    assert ticket["balance_cents"] == total // 2
    assert client.post(path + "/production/start", json={"expected_revision": 1}).status_code == 409
    assert (
        client.post(
            path + "/production/start", json={"expected_revision": ticket["revision"]}
        ).status_code
        == 200
    )
    # A fresh session verifies committed persistence beyond the HTTP response.
    with session_factory()() as db:
        persisted = db.get(Order, oid)
        assert persisted.accepted_revision_id == option["id"]
        assert persisted.production_status == "started"
