"""Exercise the actual Strands tool/structured-output path with a scripted model.

This is an SDK/application contract test, not evidence of live Bedrock inference.
"""

import asyncio
import json
from copy import deepcopy

import pytest
from botocore.exceptions import ClientError
from ordertowork.config import get_settings
from ordertowork.db import session_factory
from ordertowork.models.jobs import Job
from ordertowork.services.agent import AgentContextTooLarge, compact_order_context
from ordertowork.services.orders import order_snapshot
from ordertowork.worker import analysis_failure_message, process_one
from sqlalchemy import select
from strands.models.model import Model
from test_workflow import read_order, workspace


class ScriptedModel(Model):
    def __init__(
        self, message, *, loop=False, skip_preview=False, intent="change_request", fail_after=None
    ):
        self.step = 0
        self.message = message
        self.loop = loop
        self.skip_preview = skip_preview
        self.intent = intent
        self.fail_after = fail_after
        self.first_messages = None

    def update_config(self, **kwargs):
        pass

    def get_config(self):
        return {"model_id": "offline-strands-contract"}

    async def structured_output(self, *args, **kwargs):
        raise AssertionError("The deprecated API must not be used")
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.step += 1
        if self.fail_after is not None and self.step > self.fail_after:
            raise RuntimeError("Private provider error text must never be stored")
        if self.first_messages is None:
            self.first_messages = deepcopy(messages)
        if self.loop or (self.step == 1 and not self.skip_preview):
            name, value = (
                "preview_change",
                {
                    "quantity": 45,
                    "variant": "navy",
                    "sizes": {"S": 6, "M": 27, "L": 12},
                    "pickup_at": "2026-09-17T12:00:00-04:00",
                },
            )
        else:
            name = next(s["name"] for s in tool_specs if s["name"] == "RequestInterpretation")
            value = {
                "intent": self.intent,
                "quantity": 45,
                "variant": "navy",
                "sizes": {"S": 6, "M": 27, "L": 12},
                "pickup_at": "2026-09-17T12:00:00-04:00",
                "evidence": [{"field": "request", "quote": self.message}],
                "missing_fields": [],
            }
        yield {"messageStart": {"role": "assistant"}}
        yield {
            "contentBlockStart": {
                "start": {"toolUse": {"name": name, "toolUseId": f"call-{self.step}"}},
                "contentBlockIndex": 0,
            }
        }
        yield {
            "contentBlockDelta": {
                "delta": {"toolUse": {"input": json.dumps(value)}},
                "contentBlockIndex": 0,
            }
        }
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": "tool_use"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 500, "outputTokens": 150, "totalTokens": 650},
                "metrics": {"latencyMs": 1},
            }
        }


def enqueue_bedrock(client, wid, order_id, body):
    response = client.post(f"/api/workspaces/{wid}/orders/{order_id}/messages", json={"body": body})
    assert response.status_code == 202, response.text
    jid = response.json()["job"]["id"]
    with session_factory()() as db:
        db.get(Job, jid).mode = "bedrock"
        db.commit()
    return jid


def test_strands_reads_database_checks_constraints_and_persists_review(integrated, monkeypatch):
    import strands.models

    client = integrated.client
    wid = workspace(client)
    order = read_order(client, wid)
    body = order["messages"][-1]["body"]
    get_settings().bedrock_model_id = "offline-contract-fixture"
    model = ScriptedModel(body)
    model_config = {}

    def make_model(**kwargs):
        model_config.update(kwargs)
        return model

    monkeypatch.setattr(strands.models, "BedrockModel", make_model)
    jid = enqueue_bedrock(client, wid, order["id"], body)
    assert asyncio.run(process_one())
    with session_factory()() as db:
        job = db.scalar(select(Job).where(Job.id == jid))
        assert job.status == "succeeded", job.error
        assert [event["tool"] for event in job.tool_events] == [
            "read_order_context",
            "preview_change",
            "bedrock_usage",
        ]
        assert job.tool_events[1]["result"]["feasible"] is False
        usage = job.tool_events[-1]["result"]
        assert usage["input_tokens"] == 1000
        assert usage["output_tokens"] == 300
        assert usage["total_tokens"] == 1300
        assert usage["model_calls"] == 2
        assert usage["usage_complete"] is True
    assert model_config["max_tokens"] == get_settings().bedrock_max_output_tokens
    assert model_config["temperature"] == 0
    assert model_config["boto_client_config"].retries["total_max_attempts"] == 2
    updated = read_order(client, wid)
    assert updated["accepted_revision"]["id"] == order["accepted_revision"]["id"]
    assert (
        len([r for r in updated["revisions"] if r["status"] == "proposed" and r["feasible"]]) == 2
    )


def test_strands_turn_budget_stops_loop_without_creating_proposals(integrated, monkeypatch):
    import strands.models

    client = integrated.client
    wid = workspace(client)
    order = read_order(client, wid)
    settings = get_settings()
    settings.bedrock_model_id = "offline-contract-fixture"
    settings.agent_max_turns = 2
    model = ScriptedModel(order["messages"][-1]["body"], loop=True)
    monkeypatch.setattr(strands.models, "BedrockModel", lambda **kwargs: model)
    jid = enqueue_bedrock(client, wid, order["id"], model.message)
    assert asyncio.run(process_one())
    with session_factory()() as db:
        job = db.get(Job, jid)
        assert job.status == "failed"
        assert "token or turn limit" in job.error
        assert job.tool_events[-1]["result"]["stop_reason"] == "limit_turns"
        assert job.tool_events[-1]["result"]["total_tokens"] == 1300
    assert model.step == 2
    updated = read_order(client, wid)
    assert updated["accepted_revision"] == order["accepted_revision"]
    assert updated["revisions"] == order["revisions"]


def test_final_terms_are_checked_when_model_skips_preview(integrated, monkeypatch):
    import strands.models

    client = integrated.client
    wid = workspace(client)
    order = read_order(client, wid)
    get_settings().bedrock_model_id = "offline-contract-fixture"
    model = ScriptedModel(order["messages"][-1]["body"], skip_preview=True)
    monkeypatch.setattr(strands.models, "BedrockModel", lambda **kwargs: model)
    jid = enqueue_bedrock(client, wid, order["id"], model.message)
    assert asyncio.run(process_one())
    with session_factory()() as db:
        job = db.get(Job, jid)
        assert job.status == "succeeded", job.error
        assert [e["tool"] for e in job.tool_events] == [
            "read_order_context",
            "bedrock_usage",
            "preview_change",
        ]
        assert job.tool_events[-1]["result"]["terms"]["quantity"] == 45
        assert job.tool_events[-1]["result"]["feasible"] is False
    assert model.step == 1  # The coordinator's final check makes no additional model call.


def test_failed_stream_keeps_partial_usage_and_does_not_change_order(integrated, monkeypatch):
    import strands.models

    client = integrated.client
    wid = workspace(client)
    order = read_order(client, wid)
    get_settings().bedrock_model_id = "offline-contract-fixture"
    model = ScriptedModel(order["messages"][-1]["body"], fail_after=1)
    monkeypatch.setattr(strands.models, "BedrockModel", lambda **kwargs: model)
    jid = enqueue_bedrock(client, wid, order["id"], model.message)
    assert asyncio.run(process_one())
    with session_factory()() as db:
        job = db.get(Job, jid)
        assert job.status == "failed"
        assert "Private provider" not in job.error
        usage = job.tool_events[-1]["result"]
        assert usage["total_tokens"] == 650
        assert usage["usage_complete"] is False
        assert usage["stop_reason"] == "error_or_timeout"
    assert read_order(client, wid)["revisions"] == order["revisions"]


def test_each_review_uses_current_facts_without_replaying_previous_messages(
    integrated, monkeypatch
):
    import strands.models

    client = integrated.client
    wid = workspace(client)
    order = read_order(client, wid)
    get_settings().bedrock_model_id = "offline-contract-fixture"
    models = []
    for body in ("first private request", "second private request"):
        model = ScriptedModel(body, skip_preview=True, intent="question")
        models.append(model)
        monkeypatch.setattr(strands.models, "BedrockModel", lambda **kwargs: model)
        jid = enqueue_bedrock(client, wid, order["id"], body)
        assert asyncio.run(process_one())
        with session_factory()() as db:
            assert db.get(Job, jid).status == "succeeded"
    second_context = json.dumps(models[1].first_messages)
    assert "second private request" in second_context
    assert "first private request" not in second_context
    assert "message_received_at" in second_context
    assert "baseline_terms" in second_context
    snapshots = list((get_settings().data_dir / "agent-sessions").rglob("snapshot_latest.json"))
    assert len(snapshots) == 2
    assert read_order(client, wid)["accepted_revision"] == order["accepted_revision"]


def test_oversized_order_context_rejects_instead_of_silently_truncating(integrated):
    client = integrated.client
    wid = workspace(client)
    order = read_order(client, wid)
    with session_factory()() as db:
        snapshot = order_snapshot(db, wid, order["id"])
    snapshot["accepted_revision"]["terms"]["specification"] = {"details": "x" * 24001}
    with pytest.raises(AgentContextTooLarge):
        compact_order_context(snapshot)


@pytest.mark.parametrize(
    "code,expected",
    [
        ("ExpiredTokenException", "Reauthenticate"),
        ("AccessDeniedException", "worker role"),
        ("ThrottlingException", "rate limited"),
        ("ValidationException", "model ID and region"),
    ],
)
def test_worker_explains_aws_setup_failures_without_raw_provider_text(code, expected):
    provider = ClientError({"Error": {"Code": code, "Message": "PRIVATE SDK DETAILS"}}, "Converse")
    wrapped = RuntimeError("PRIVATE WRAPPER DETAILS")
    wrapped.__cause__ = provider
    error = analysis_failure_message(wrapped)
    assert expected in error
    assert "PRIVATE" not in error
