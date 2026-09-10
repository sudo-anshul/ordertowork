import pytest
from fastapi.testclient import TestClient
from ordertowork.config import Settings
from ordertowork.main import create_app
from pydantic import ValidationError


def test_health_and_security_headers():
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert client.get("/api/does-not-exist").status_code == 404


def test_cross_origin_mutations_rejected():
    client = TestClient(create_app())
    response = client.post("/api/auth/development-login", headers={"origin": "https://attacker.invalid"})
    assert response.status_code == 403


def test_production_fails_closed_without_real_auth():
    with pytest.raises(ValidationError, match="Cognito"):
        Settings(environment="production", auth_mode="development", _env_file=None)
