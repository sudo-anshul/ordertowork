"""Exercise the actual Strands tool/structured-output path with a scripted model.

This is an SDK/application contract test, not evidence of live Bedrock inference.
"""

import asyncio
import json

from ordertowork.config import get_settings
from ordertowork.db import session_factory
from ordertowork.models.jobs import Job
from ordertowork.worker import process_one
from sqlalchemy import select
from strands.models.model import Model
from test_workflow import read_order, workspace


class ScriptedModel(Model):
    def __init__(self, message):
        self.step = 0
        self.message = message

    def update_config(self, **kwargs):
        pass

    def get_config(self):
        return {"model_id": "offline-strands-contract"}

    async def structured_output(self, *args, **kwargs):
        raise AssertionError("The deprecated API must not be used")
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.step += 1
        if self.step == 1:
            name, value = "read_order_context", {}
        elif self.step == 2:
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
                "intent": "change_request",
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


def test_strands_reads_database_checks_constraints_and_persists_review(integrated, monkeypatch):
    import strands.models

    client = integrated.client
    wid = workspace(client)
    order = read_order(client, wid)
    body = order["messages"][-1]["body"]
    get_settings().bedrock_model_id = "offline-contract-fixture"
    model = ScriptedModel(body)
    monkeypatch.setattr(strands.models, "BedrockModel", lambda **kwargs: model)
    response = client.post(
        f"/api/workspaces/{wid}/orders/{order['id']}/messages", json={"body": body}
    )
    jid = response.json()["job"]["id"]
    with session_factory()() as db:
        db.get(Job, jid).mode = "bedrock"
        db.commit()
    assert asyncio.run(process_one())
    with session_factory()() as db:
        job = db.scalar(select(Job).where(Job.id == jid))
        assert job.status == "succeeded", job.error
        assert [event["tool"] for event in job.tool_events] == [
            "read_order_context",
            "preview_change",
        ]
        assert job.tool_events[-1]["result"]["feasible"] is False
    updated = read_order(client, wid)
    assert updated["accepted_revision"]["id"] == order["accepted_revision"]["id"]
    assert (
        len([r for r in updated["revisions"] if r["status"] == "proposed" and r["feasible"]]) == 2
    )
