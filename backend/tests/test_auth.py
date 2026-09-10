from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from ordertowork.api import auth as auth_api
from ordertowork.api import workspaces as workspaces_api
from ordertowork.config import Settings
from ordertowork.db import Base, get_db, utcnow
from ordertowork.models.auth import AuthSession, OAuthLoginState
from ordertowork.models.core import Membership, User, Workspace
from ordertowork.services import auth as auth_service
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def identity_app(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    tables = [
        User.__table__,
        Workspace.__table__,
        Membership.__table__,
        AuthSession.__table__,
        OAuthLoginState.__table__,
        auth_service.AuthAuditEvent.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    factory = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,
        environment="development",
        auth_mode="development",
        app_url="http://localhost:5173",
        platform_admin_subjects="",
        storage_mode="local",
        agent_mode="disabled",
    )
    for module in (auth_service, auth_api, workspaces_api):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(auth_api.router)
    app.include_router(workspaces_api.router)
    app.include_router(workspaces_api.platform_router)

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app, base_url="http://localhost:5173", client=("127.0.0.1", 42000)) as client:
        yield SimpleNamespace(app=app, client=client, settings=settings, factory=factory)
    engine.dispose()


def login(client, email="owner@example.com", name="Owner"):
    response = client.post("/api/auth/development-login", json={"email": email, "name": name})
    assert response.status_code == 200, response.text
    body = response.json()
    client.headers["X-CSRF-Token"] = body["csrf_token"]
    return body


def workspace_for(context, user_id, name="First business", role="owner"):
    with context.factory() as db:
        workspace = Workspace(name=name, profile="merchandise", is_demo=False)
        db.add(workspace)
        db.flush()
        membership = Membership(workspace_id=workspace.id, user_id=user_id, role=role)
        db.add(membership)
        db.commit()
        return workspace.id, membership.id


def use_cognito(context):
    context.settings.auth_mode = "cognito"
    context.settings.app_url = "https://orders.example"
    context.settings.cognito_region = "us-east-1"
    context.settings.cognito_user_pool_id = "us-east-1_Example"
    context.settings.cognito_client_id = "example-client"
    context.settings.cognito_domain = "https://example.auth.us-east-1.amazoncognito.com"
    context.client.base_url = "https://orders.example"


def signing_setup(context, monkeypatch, nonce):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        auth_service,
        "jwks_client",
        lambda issuer: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())
        ),
    )
    _, issuer, _ = auth_service.cognito_configuration(context.settings)
    claims = {
        "iss": issuer,
        "aud": "example-client",
        "sub": "validated-subject",
        "email": "verified@example.com",
        "email_verified": True,
        "name": "Verified Owner",
        "token_use": "id",
        "nonce": nonce,
        "iat": utcnow(),
        "exp": utcnow() + timedelta(minutes=10),
    }
    return key, claims


def test_opaque_session_csrf_origin_and_logout(identity_app):
    context = identity_app
    assert context.client.get("/api/auth/me").status_code == 401
    payload = login(context.client)
    raw = context.client.cookies.get("otw_session")
    with context.factory() as db:
        session = db.scalar(select(AuthSession))
        assert session.token_hash == auth_service.digest(raw)
        assert session.token_hash != raw
        assert session.csrf_token == payload["csrf_token"]
    response = context.client.get("/api/auth/me")
    assert response.headers["cache-control"] == "no-store"
    assert not response.json()["user"]["email_verified"]
    assert not response.json()["user"]["is_platform_admin"]
    assert (
        context.client.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    assert (
        context.client.post(
            "/api/auth/logout", headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert context.client.post("/api/auth/logout").status_code == 200
    context.client.cookies.set("otw_session", raw)
    assert context.client.get("/api/auth/me").status_code == 401


def test_expired_session_is_rejected(identity_app):
    login(identity_app.client)
    with identity_app.factory() as db:
        session = db.scalar(select(AuthSession))
        session.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert identity_app.client.get("/api/auth/me").status_code == 401


def test_session_rotation_revokes_previous_cookie(identity_app):
    login(identity_app.client)
    first_cookie = identity_app.client.cookies.get("otw_session")
    login(identity_app.client)
    with identity_app.factory() as db:
        old = db.scalar(
            select(AuthSession).where(AuthSession.token_hash == auth_service.digest(first_cookie))
        )
        assert old.revoked_at is not None


def test_development_login_fails_closed(identity_app):
    context = identity_app
    body = {"email": "owner@example.com", "name": "Owner"}
    with TestClient(
        context.app, base_url="http://localhost:5173", client=("192.0.2.3", 50000)
    ) as remote:
        assert remote.post("/api/auth/development-login", json=body).status_code == 404
    with TestClient(
        context.app, base_url="http://public.example", client=("127.0.0.1", 50000)
    ) as public:
        assert public.post("/api/auth/development-login", json=body).status_code == 404
    context.settings.environment = "staging"
    assert context.client.post("/api/auth/development-login", json=body).status_code == 404
    context.settings.environment = "production"
    context.settings.auth_mode = "cognito"
    assert context.client.post("/api/auth/development-login", json=body).status_code == 404


def test_cannot_self_assign_admin_or_reuse_stored_flag(identity_app):
    client = identity_app.client
    response = client.post(
        "/api/auth/development-login",
        json={
            "email": "owner@example.com",
            "name": "Owner",
            "is_platform_admin": True,
        },
    )
    assert response.status_code == 422
    body = login(client)
    with identity_app.factory() as db:
        db.get(User, body["user"]["id"]).is_platform_admin = True
        db.commit()
    assert client.get("/api/platform/overview").status_code == 403
    assert not client.get("/api/auth/me").json()["user"]["is_platform_admin"]
    identity_app.settings.platform_admin_subjects = "development:owner@example.com"
    response = client.get("/api/platform/overview")
    assert response.status_code == 200
    assert response.json()["counts"]["users"] == 1
    assert "csrf_token" not in str(response.json())


def test_membership_isolation_roles_and_immediate_revocation(identity_app):
    context = identity_app
    owner = login(context.client)
    workspace_id, owner_member_id = workspace_for(context, owner["user"]["id"])
    owner_cookie = context.client.cookies.get("otw_session")
    operator = login(context.client, "operator@example.com", "Operator")
    second_workspace, _ = workspace_for(context, operator["user"]["id"], "Second business")
    # This actor cannot discover another tenant by an ID.
    assert context.client.get(f"/api/workspaces/{workspace_id}").status_code == 404
    with context.factory() as db:
        member = Membership(
            workspace_id=workspace_id, user_id=operator["user"]["id"], role="operator"
        )
        db.add(member)
        db.commit()
        member_id = member.id
    assert context.client.get(f"/api/workspaces/{workspace_id}").status_code == 200
    assert (
        context.client.patch(f"/api/workspaces/{workspace_id}", json={"name": "Hacked"}).status_code
        == 403
    )
    assert (
        context.client.patch(
            f"/api/workspaces/{workspace_id}/members/{member_id}", json={"role": "owner"}
        ).status_code
        == 403
    )
    # Login rotation revoked the former cookie; a fresh owner session is required.
    assert owner_cookie
    login(context.client)
    assert (
        context.client.delete(
            f"/api/workspaces/{workspace_id}/members/{owner_member_id}"
        ).status_code
        == 409
    )
    assert (
        context.client.patch(
            f"/api/workspaces/{workspace_id}/members/{owner_member_id}", json={"role": "operator"}
        ).status_code
        == 409
    )
    assert (
        context.client.delete(f"/api/workspaces/{workspace_id}/members/{member_id}").status_code
        == 200
    )
    login(context.client, "operator@example.com", "Operator")
    assert context.client.get(f"/api/workspaces/{workspace_id}").status_code == 404
    assert context.client.get(f"/api/workspaces/{second_workspace}").status_code == 200


def test_workspace_settings_validation_and_archive(identity_app):
    context = identity_app
    owner = login(context.client)
    workspace_id, _ = workspace_for(context, owner["user"]["id"])
    for body in (
        {"timezone": "Not/AZone"},
        {"deposit_bps": 10001},
        {"deposit_bps": True},
        {"profile": "bakery"},
        {"name": None},
        {},
    ):
        assert context.client.patch(f"/api/workspaces/{workspace_id}", json=body).status_code == 422
    response = context.client.patch(
        f"/api/workspaces/{workspace_id}", json={"timezone": "Asia/Kolkata", "deposit_bps": 2500}
    )
    assert response.status_code == 200
    assert response.json()["timezone"] == "Asia/Kolkata"
    assert (
        context.client.request(
            "DELETE",
            f"/api/workspaces/{workspace_id}",
            json={
                "confirm_name": "wrong",
            },
        ).status_code
        == 422
    )
    assert (
        context.client.request(
            "DELETE",
            f"/api/workspaces/{workspace_id}",
            json={
                "confirm_name": "First business",
            },
        ).status_code
        == 200
    )
    assert context.client.get(f"/api/workspaces/{workspace_id}").status_code == 409


def test_cognito_login_uses_pkce_browser_state_and_secure_cookie(identity_app):
    context = identity_app
    use_cognito(context)
    response = context.client.get("/api/auth/login", follow_redirects=False)
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://orders.example/api/auth/callback"]
    assert "Secure" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    with context.factory() as db:
        pending = db.scalar(select(OAuthLoginState))
        assert pending.state_hash == auth_service.digest(query["state"][0])
        assert pending.nonce_hash == auth_service.digest(query["nonce"][0])
        assert query["code_challenge"] == [auth_service.pkce_challenge(pending.code_verifier)]
    assert context.client.get("/api/auth/callback?code=fake&state=mismatch").status_code == 400


@pytest.mark.parametrize(
    "change",
    [
        {"aud": "foreign-client"},
        {"iss": "https://evil.example"},
        {"nonce": "wrong"},
        {"token_use": "access"},
        {"email_verified": False},
        {"exp": 1000},
        {"aud": ["example-client"]},
    ],
)
def test_cognito_rejects_invalid_identity_claims(identity_app, monkeypatch, change):
    context = identity_app
    use_cognito(context)
    key, claims = signing_setup(context, monkeypatch, "expected-nonce")
    claims.update(change)
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})
    with pytest.raises(HTTPException):
        auth_service.validate_id_token(
            token, auth_service.digest("expected-nonce"), context.settings
        )


def test_cognito_callback_verifies_jwt_and_consumes_state_once(identity_app, monkeypatch):
    context = identity_app
    use_cognito(context)
    start = context.client.get("/api/auth/login", follow_redirects=False)
    query = parse_qs(urlsplit(start.headers["location"]).query)
    state, nonce = query["state"][0], query["nonce"][0]
    key, claims = signing_setup(context, monkeypatch, nonce)
    token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})
    calls = []

    async def fake_exchange(code, verifier, settings):
        calls.append((code, verifier))
        return {"id_token": token}

    monkeypatch.setattr(auth_api, "exchange_code", fake_exchange)
    response = context.client.get(
        "/api/auth/callback",
        params={"state": state, "code": "provider-code"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert "Secure" in response.headers["set-cookie"]
    assert len(calls) == 1 and len(calls[0][1]) >= 43
    me = context.client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email_verified"] is True
    assert me.json()["user"]["email"] == "verified@example.com"
    with context.factory() as db:
        assert db.scalar(select(OAuthLoginState)).code_verifier == ""
        session = db.scalar(select(AuthSession))
        assert session.auth_method == "cognito"
        assert session.token_hash != token
    context.client.cookies.set("otw_login_state", state, path="/api/auth")
    replay = context.client.get(
        "/api/auth/callback",
        params={"state": state, "code": "provider-code"},
        follow_redirects=False,
    )
    assert replay.status_code == 400
    assert len(calls) == 1
