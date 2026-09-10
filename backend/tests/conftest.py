from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from ordertowork.config import get_settings
from ordertowork.db import Base, get_engine
from ordertowork.main import create_app


@pytest.fixture
def integrated(monkeypatch, tmp_path):
    for key, value in {
        "OTW_DATABASE_URL": f"sqlite:///{tmp_path / 'integration.db'}",
        "OTW_ENVIRONMENT": "development",
        "OTW_AUTH_MODE": "development",
        "OTW_APP_URL": "http://localhost:5173",
        "OTW_AGENT_MODE": "reference",
        "OTW_BEDROCK_ENDPOINT": "runtime",
        "OTW_STORAGE_MODE": "local",
        "OTW_DATA_DIR": str(tmp_path / "data"),
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    get_engine.cache_clear()
    Base.metadata.create_all(get_engine())
    with TestClient(
        create_app(), base_url="http://localhost:5173", client=("127.0.0.1", 40000)
    ) as client:
        response = client.post(
            "/api/auth/development-login", json={"email": "owner@example.com", "name": "Owner"}
        )
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        yield SimpleNamespace(client=client)
    get_engine().dispose()
    get_engine.cache_clear()
    get_settings.cache_clear()
