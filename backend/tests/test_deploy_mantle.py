"""Release configuration must preserve billing controls and reject key handoffs."""

import importlib.util
import json
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def deployment(monkeypatch):
    monkeypatch.syspath_prepend(str(DEPLOY))
    return load_module("release_test", DEPLOY / "aws_release.py")


@pytest.fixture
def manifests():
    host = {
        "repository_uri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/ordertowork",
        "domain": "orders.example.com",
        "region": "us-east-1",
    }
    services = {
        "resource_names": {"bucket": "ordertowork-test-private"},
        "app_env": {
            "OTW_S3_BUCKET": "ordertowork-test-private",
            "OTW_COGNITO_REGION": "us-east-1",
            "OTW_COGNITO_USER_POOL_ID": "us-east-1_example",
            "OTW_COGNITO_CLIENT_ID": "exampleclient",
            "OTW_COGNITO_DOMAIN": "https://example.auth.us-east-1.amazoncognito.com",
        },
    }
    return host, services


def test_release_defaults_preserve_runtime_compatibility(deployment, manifests):
    settings = deployment.release_config(*manifests, "sha256:" + "a" * 64)
    assert settings["OTW_BEDROCK_ENDPOINT"] == "runtime"
    assert settings["OTW_BEDROCK_MODEL_ID"] == "us.amazon.nova-lite-v1:0"


def test_mantle_release_preserves_deliberate_model_and_budget(deployment, manifests, tmp_path):
    host, services = manifests
    services["app_env"].update(
        OTW_BEDROCK_ENDPOINT="mantle",
        OTW_BEDROCK_MODEL_ID="openai.gpt-oss-120b",
        OTW_BEDROCK_MANTLE_PROJECT_ID="demo-project",
        OTW_MAX_DAILY_BEDROCK_ATTEMPTS="0",
        OTW_MAX_DAILY_DEMO_BEDROCK_ATTEMPTS="0",
    )
    settings = deployment.release_config(host, services, "sha256:" + "a" * 64)
    assert settings["OTW_BEDROCK_MODEL_ID"] == "openai.gpt-oss-120b"
    module = deployment.budget_module()
    module.write_config(settings, tmp_path)
    runtime = module.read_generated_env(tmp_path / "runtime.env")
    assert runtime["OTW_AGENT_MODE"] == "bedrock"
    assert runtime["OTW_BEDROCK_ENDPOINT"] == "mantle"
    assert runtime["OTW_BEDROCK_MANTLE_PROJECT_ID"] == "demo-project"
    assert runtime["OTW_MAX_DAILY_BEDROCK_ATTEMPTS"] == "0"
    assert runtime["OTW_MAX_DAILY_DEMO_BEDROCK_ATTEMPTS"] == "0"
    assert not any("API_KEY" in key or "ACCESS_KEY" in key for key in runtime)


def test_explicit_mantle_uses_selected_model_default(deployment, manifests):
    manifests[1]["app_env"]["OTW_BEDROCK_ENDPOINT"] = "mantle"
    settings = deployment.release_config(*manifests, "sha256:" + "a" * 64)
    assert settings["OTW_BEDROCK_MODEL_ID"] == "qwen.qwen3-235b-a22b-2507"
    assert settings["OTW_BEDROCK_MANTLE_PROJECT_ID"] == "default"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("OTW_BEDROCK_ENDPOINT", "https://untrusted.example"),
        ("OTW_BEDROCK_MANTLE_PROJECT_ID", "*"),
        ("OPENAI_API_KEY", "private-test-token"),
        ("AWS_BEARER_TOKEN_BEDROCK", "private-test-token"),
    ],
)
def test_config_rejects_bad_endpoint_project_and_secrets(
    deployment, manifests, tmp_path, key, value
):
    settings = deployment.release_config(*manifests, "sha256:" + "a" * 64)
    settings[key] = value
    path = tmp_path / "config.json"
    path.write_text(json.dumps(settings))
    with pytest.raises(ValueError):
        deployment.budget_module().load_config(path)


def test_mantle_policy_scopes_model_project_region_and_short_term_tokens():
    module = load_module("mantle_policy_test", DEPLOY / "mantle_policy.py")
    policy = module.mantle_policy("123456789012", "us-east-1")
    inference, token = policy["Statement"]
    assert inference["Action"] == "bedrock-mantle:CreateInference"
    assert inference["Resource"] == (
        "arn:aws:bedrock-mantle:us-east-1:123456789012:project/default"
    )
    assert inference["Condition"]["StringEquals"]["bedrock-mantle:Model"] == (
        "qwen.qwen3-235b-a22b-2507"
    )
    assert token["Action"] == "bedrock-mantle:CallWithBearerToken"
    assert token["Condition"]["StringEquals"] == {
        "bedrock-mantle:BearerTokenType": "SHORT_TERM",
        "aws:RequestedRegion": "us-east-1",
    }
    for invalid in ("*", "qwen.*", "qwen/model"):
        with pytest.raises(ValueError):
            module.mantle_policy("123456789012", "us-east-1", invalid)
    with pytest.raises(ValueError):
        module.mantle_policy("123456789012", "us-east-1", project="*")
