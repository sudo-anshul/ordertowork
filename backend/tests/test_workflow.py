"""Integrated local workflow and worker tests; reference mode never calls a model."""

import asyncio
from datetime import timedelta

import pytest
from ordertowork.db import session_factory, utcnow
from ordertowork.models.domain import Order, SourceMessage
from ordertowork.models.jobs import Job
from ordertowork.services.jobs import claim_next, enqueue_analysis
from ordertowork.worker import process_one
from sqlalchemy import select


def workspace(client, profile="merchandise"):
    response = client.post(
        "/api/workspaces", json={"name": "Sample " + profile, "profile": profile, "seed_demo": True}
    )
    assert response.status_code == 201, response.text
    data = response.json()
    return data.get("workspace", data)["id"]


def read_order(client, wid):
    response = client.get(f"/api/workspaces/{wid}/orders")
    assert response.status_code == 200, response.text
    oid = response.json()["orders"][0]["id"]
    return client.get(f"/api/workspaces/{wid}/orders/{oid}").json()


@pytest.mark.parametrize(
    "profile,quantity,total,top_up",
    [("merchandise", 45, 81000, 13500), ("bakery", 36, 14400, 2400)],
)
def test_real_http_flow_queued_analysis_customer_approval_and_ticket(
    integrated, profile, quantity, total, top_up
):
    client = integrated.client
    wid = workspace(client, profile)
    original = read_order(client, wid)
    path = f"/api/workspaces/{wid}/orders/{original['id']}"
    response = client.post(path + "/messages", json={"body": original["messages"][-1]["body"]})
    assert response.status_code == 202, response.text
    job_id = response.json()["job"]["id"]
    assert asyncio.run(process_one())
    job = client.get(f"/api/workspaces/{wid}/jobs/{job_id}").json()
    assert job["status"] == "succeeded", job
    assert job["mode"] == "reference"
    assert any(event["tool"] == "preview_change" for event in job["tool_events"]), job
    detail = client.get(path).json()
    assert detail["accepted_revision"]["id"] == original["accepted_revision"]["id"]
    choices = [r for r in detail["revisions"] if r["status"] == "proposed" and r["feasible"]]
    selected = next(
        r
        for r in choices
        if r["terms"]["quantity"] == quantity and r["terms"]["total_cents"] == total
    )
    share = client.post(path + f"/proposals/{selected['id']}/share").json()
    token = share["url"].rsplit("/", 1)[1]
    customer_path = f"/api/customer/{token}"
    view = client.get(customer_path).json()
    assert view["top_up_cents"] == top_up
    assert (
        client.post(
            customer_path + "/approve", json={"terms_hash": share["terms_hash"], "consent": False}
        ).status_code
        == 422
    )
    assert (
        client.post(
            customer_path + "/approve", json={"terms_hash": share["terms_hash"], "consent": True}
        ).status_code
        == 200
    )
    assert client.get(path + "/ticket").status_code == 409
    payment = {
        "amount_cents": top_up,
        "reference": "TEST manually recorded transfer",
        "idempotency_key": "test-transfer",
    }
    assert client.post(path + "/deposits", json=payment).status_code == 200
    assert client.post(path + "/deposits", json=payment).status_code == 200
    ticket = client.get(path + "/ticket").json()
    assert ticket["terms"]["total_cents"] == total
    assert ticket["balance_cents"] == total // 2
    assert client.post(path + "/production/start", json={}).status_code == 200


def test_job_claim_serializes_order_and_recovers_expired_lease(integrated):
    wid = workspace(integrated.client)
    order = read_order(integrated.client, wid)
    with session_factory()() as db:
        source = db.scalar(select(SourceMessage).where(SourceMessage.order_id == order["id"]))
        first = enqueue_analysis(db, wid, order["id"], source.id)
        second_message = SourceMessage(
            workspace_id=wid, order_id=order["id"], body="Another request"
        )
        db.add(second_message)
        db.flush()
        second = enqueue_analysis(db, wid, order["id"], second_message.id)
        db.commit()
        first_claim = claim_next(db)
        db.commit()
        assert first_claim[0] == first.id
        assert claim_next(db) is None
        db.commit()
        first.leased_until = utcnow() - timedelta(seconds=1)
        db.commit()
        recovered = claim_next(db)
        db.commit()
        assert recovered[0] == first.id and recovered[1] != first_claim[1]
        assert db.get(Job, second.id).status == "queued"


def test_private_attachments_are_scoped_and_reject_executable_content(integrated):
    client = integrated.client
    wid = workspace(client)
    order = read_order(client, wid)
    path = f"/api/workspaces/{wid}/orders/{order['id']}/files"
    response = client.post(
        path, files={"file": ("proof.pdf", b"%PDF-1.4\nfixture", "application/pdf")}
    )
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    downloaded = client.get(path + f"/{identifier}/download")
    assert downloaded.status_code == 200
    assert "attachment" in downloaded.headers["content-disposition"]
    assert downloaded.content == b"%PDF-1.4\nfixture"
    assert (
        client.post(
            path, files={"file": ("bad.png", b"<script>bad</script>", "image/png")}
        ).status_code
        == 415
    )
    outsider = client.post(
        "/api/auth/development-login", json={"email": "outsider@example.com", "name": "Outsider"}
    )
    client.headers["X-CSRF-Token"] = outsider.json()["csrf_token"]
    assert client.get(path + f"/{identifier}/download").status_code == 404


def test_bedrock_missing_configuration_fails_without_changing_order(integrated):
    wid = workspace(integrated.client)
    order = read_order(integrated.client, wid)
    response = integrated.client.post(
        f"/api/workspaces/{wid}/orders/{order['id']}/messages",
        json={"body": "Could we add 15 medium shirts?"},
    )
    jid = response.json()["job"]["id"]
    with session_factory()() as db:
        db.get(Job, jid).mode = "bedrock"
        db.commit()
    assert asyncio.run(process_one())
    with session_factory()() as db:
        job = db.get(Job, jid)
        assert job.status == "failed"
        assert "not configured" in job.error
        assert db.get(Order, order["id"]).accepted_revision_id == order["accepted_revision"]["id"]
