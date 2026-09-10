"""Mantle authentication/configuration, budget compatibility and extracted-fact guards."""

import asyncio
from datetime import timedelta

import httpx
import pytest
from ordertowork.config import Settings, get_settings
from ordertowork.db import session_factory
from ordertowork.models.jobs import Job
from ordertowork.services.agent import Evidence, RequestInterpretation, validate_interpretation
from ordertowork.services.bedrock import create_bedrock_model
from ordertowork.worker import analysis_failure_message, process_one
from pydantic import ValidationError
from test_strands_integration import ScriptedModel, enqueue_bedrock
from test_workflow import read_order, workspace


def test_mantle_refreshes_aws_token_per_request_without_static_keys(monkeypatch):
    import aws_bedrock_token_generator

    calls = []

    def provide_token(**kwargs):
        calls.append(kwargs)
        return f"synthetic-token-{len(calls)}"

    monkeypatch.setattr(aws_bedrock_token_generator, "provide_token", provide_token)
    settings = Settings(
        _env_file=None, bedrock_endpoint="mantle", bedrock_model_id="qwen.qwen3-235b-a22b-2507"
    )
    model = create_bedrock_model(settings)
    first, second = model._resolve_client_args(), model._resolve_client_args()
    assert first["api_key"] != second["api_key"]
    assert first["base_url"] == "https://bedrock-mantle.us-east-1.api.aws/v1"
    assert first["project"] == "default"
    assert first["max_retries"] == 0
    assert all(call == {"region": "us-east-1", "expiry": timedelta(minutes=15)} for call in calls)
    assert model.config["params"]["max_completion_tokens"] == settings.bedrock_max_output_tokens
    assert model.config["params"]["parallel_tool_calls"] is False
    assert "api_key" not in model.client_args


def test_endpoint_configuration_is_explicit():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, bedrock_endpoint="https://arbitrary.example")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, bedrock_mantle_project_id="invalid\nproject")


def test_mantle_uses_actual_strands_workflow_and_persists_model_telemetry(integrated, monkeypatch):
    import strands.models.openai

    client = integrated.client
    wid = workspace(client)
    initial = read_order(client, wid)
    message = initial["messages"][-1]["body"]
    settings = get_settings()
    settings.bedrock_endpoint = "mantle"
    settings.bedrock_model_id = "qwen.qwen3-235b-a22b-2507"
    model = ScriptedModel(message)
    monkeypatch.setattr(strands.models.openai, "OpenAIModel", lambda **kwargs: model)
    jid = enqueue_bedrock(client, wid, initial["id"], message)
    assert asyncio.run(process_one())
    with session_factory()() as db:
        job = db.get(Job, jid)
        assert job.status == "succeeded", job.error
        usage = next(e["result"] for e in job.tool_events if e["tool"] == "bedrock_usage")
        assert usage["endpoint"] == "mantle"
        assert usage["model_id"] == settings.bedrock_model_id
        assert usage["model_calls"] == 2
        assert usage["total_tokens"] == 1300
    updated = read_order(client, wid)
    assert updated["accepted_revision"]["id"] == initial["accepted_revision"]["id"]
    assert (
        len([r for r in updated["revisions"] if r["status"] == "proposed" and r["feasible"]]) == 2
    )


@pytest.mark.parametrize(
    "error_name,status,expected",
    [
        ("AuthenticationError", 401, "AWS denied model access"),
        ("PermissionDeniedError", 403, "AWS denied model access"),
        ("RateLimitError", 429, "rate limited"),
        ("BadRequestError", 400, "endpoint, model ID and region"),
    ],
)
def test_provider_errors_are_actionable_without_leaking_error_text(error_name, status, expected):
    import openai

    response = httpx.Response(
        status,
        request=httpx.Request(
            "POST", "https://bedrock-mantle.us-east-1.api.aws/v1/chat/completions"
        ),
    )
    error = getattr(openai, error_name)(
        "PRIVATE PROVIDER DETAILS", response=response, body={"error": "PRIVATE"}
    )
    message = analysis_failure_message(error)
    assert expected in message
    assert "PRIVATE" not in message


@pytest.fixture
def context():
    return {
        "workspace": {"timezone": "America/New_York"},
        "order": {
            "baseline_terms": {
                "quantity": 30,
                "sizes": {"S": 6, "M": 12, "L": 12},
                "pickup_at": "2026-09-18T16:00:00-04:00",
            }
        },
    }


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"quantity": 60}, "size quantities"),
        ({"quantity": 45, "sizes": {"S": 11, "M": 22}}, "size quantities"),
        ({"pickup_at": "2026-09-18T16:00:00-04:04"}, "business timezone"),
        ({"pickup_at": "2026-09-18T16:00:00"}, "business timezone"),
        ({"pickup_at": "tomorrow"}, "business timezone"),
        ({"evidence": []}, "source quotes"),
        ({"evidence": [Evidence(field="pickup_at", quote="same pickup")]}, "could not be matched"),
    ],
)
def test_unreliable_extractions_require_clarification(context, changes, expected):
    values = {
        "intent": "change_request",
        "evidence": [Evidence(field="request", quote="same navy and pickup")],
    }
    review = RequestInterpretation(**(values | changes))
    checked = validate_interpretation(review, context, "same navy and pickup")
    assert any(expected in field for field in checked.missing_fields)


def test_valid_complete_change_preserves_business_timestamp(context):
    review = RequestInterpretation(
        intent="change_request",
        quantity=45,
        sizes={"S": 11, "M": 22, "L": 12},
        pickup_at="2026-09-18T16:00:00-04:00",
        evidence=[Evidence(field="sizes", quote="Add 5 small and 10 medium")],
    )
    assert not validate_interpretation(review, context, "Add 5 small and 10 medium").missing_fields
