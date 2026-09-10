import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from ordertowork.config import get_settings
from ordertowork.db import get_db, utcnow
from ordertowork.models.auth import AuthSession, OAuthLoginState
from ordertowork.models.core import User
from ordertowork.services.auth import (
    Actor,
    actor_payload,
    audit,
    check_development_request,
    cognito_configuration,
    cognito_logout_url,
    development_enabled,
    digest,
    exchange_code,
    fail,
    get_actor,
    issue_session,
    normalize_email,
    pkce_challenge,
    platform_admin,
    validate_id_token,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/auth", tags=["authentication"])
OAUTH_COOKIE = "otw_login_state"


class DevelopmentLogin(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=243)
    name: str = Field(min_length=1, max_length=120)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        return normalize_email(value)


def revoke_existing_session(request: Request, db: Session) -> None:
    raw = request.cookies.get(get_settings().session_cookie)
    if raw:
        db.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == digest(raw), AuthSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )


@router.get("/config")
def auth_config(request: Request) -> dict:
    settings = get_settings()
    configured = development_enabled(settings)
    if settings.auth_mode == "cognito":
        try:
            cognito_configuration(settings)
            configured = True
        except HTTPException:
            configured = False
    local_enabled = False
    if configured and development_enabled(settings):
        try:
            check_development_request(request, settings)
            local_enabled = True
        except HTTPException:
            pass
    return {
        "auth_mode": settings.auth_mode,
        "development_login_enabled": local_enabled,
        "login_url": "/api/auth/login" if settings.auth_mode == "cognito" else None,
        "configured": configured,
    }


@router.get("/me")
def me(
    response: Response, actor: Actor = Depends(get_actor), db: Session = Depends(get_db)
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return actor_payload(db, actor)


@router.post("/development-login")
def development_login(
    body: DevelopmentLogin,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    check_development_request(request, settings)
    subject = "development:" + body.email
    user = db.scalar(select(User).where(User.provider_subject == subject))
    if user is None:
        user = User(
            provider_subject=subject, email=body.email, name=body.name, email_verified=False
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            user = db.scalar(select(User).where(User.provider_subject == subject))
            if user is None:
                raise
    user.name = body.name
    user.is_platform_admin = platform_admin(user, settings)
    revoke_existing_session(request, db)
    session = issue_session(db, user, "development", response)
    audit(db, "auth.development_login", user.id)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return actor_payload(db, Actor(user, session.csrf_token, session))


@router.get("/login")
def login(db: Session = Depends(get_db)) -> Response:
    settings = get_settings()
    domain, _, redirect_uri = cognito_configuration(settings)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    db.add(
        OAuthLoginState(
            state_hash=digest(state),
            nonce_hash=digest(nonce),
            code_verifier=verifier,
            expires_at=utcnow() + timedelta(minutes=10),
        )
    )
    db.commit()
    location = (
        domain
        + "/oauth2/authorize?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": settings.cognito_client_id,
                "redirect_uri": redirect_uri,
                "scope": "openid email profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
    )
    response = RedirectResponse(location, status_code=302)
    response.set_cookie(
        OAUTH_COOKIE,
        state,
        max_age=600,
        secure=urlsplit(settings.app_url).scheme == "https",
        httponly=True,
        samesite="lax",
        path="/api/auth",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    settings = get_settings()
    cognito_configuration(settings)
    cookie_state = request.cookies.get(OAUTH_COOKIE, "")
    if not state or len(state) > 256 or not secrets.compare_digest(state, cookie_state):
        raise fail(400, "invalid_login_state", "Login expired or the browser state did not match")
    pending = db.scalar(select(OAuthLoginState).where(OAuthLoginState.state_hash == digest(state)))
    if pending is None:
        raise fail(400, "invalid_login_state", "Login expired. Please start again")
    nonce_hash, verifier = pending.nonce_hash, pending.code_verifier
    claimed = db.execute(
        update(OAuthLoginState)
        .where(
            OAuthLoginState.id == pending.id,
            OAuthLoginState.consumed_at.is_(None),
            OAuthLoginState.expires_at > utcnow(),
        )
        .values(consumed_at=utcnow(), code_verifier="")
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise fail(400, "invalid_login_state", "This login attempt expired or was already used")
    # Consume state before the network exchange; no transaction spans an external request.
    db.commit()
    if error or not code or len(code) > 8192:
        raise fail(400, "login_cancelled", "Login was not completed. Please start again")
    tokens = await exchange_code(code, verifier, settings)
    claims = await run_in_threadpool(validate_id_token, tokens["id_token"], nonce_hash, settings)
    user = db.scalar(select(User).where(User.provider_subject == claims["sub"]))
    name = str(claims.get("name") or "").strip()[:120] or claims["email"].split("@")[0][:120]
    if user is None:
        user = User(
            provider_subject=claims["sub"],
            email=claims["email"],
            name=name,
            email_verified=True,
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            user = db.scalar(select(User).where(User.provider_subject == claims["sub"]))
            if user is None:
                raise
    user.email = claims["email"]
    user.email_verified = True
    user.name = name
    user.is_platform_admin = platform_admin(user, settings)
    response = RedirectResponse(settings.app_url.rstrip("/") + "/", status_code=303)
    revoke_existing_session(request, db)
    issue_session(db, user, "cognito", response)
    audit(db, "auth.cognito_login", user.id)
    db.commit()
    response.delete_cookie(OAUTH_COOKIE, path="/api/auth")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/logout")
def logout(
    response: Response,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    if actor.session is not None:
        actor.session.revoked_at = utcnow()
    audit(db, "auth.logout", actor.user.id)
    db.commit()
    settings = get_settings()
    response.delete_cookie(
        settings.session_cookie,
        path="/",
        secure=urlsplit(settings.app_url).scheme == "https",
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, "logout_url": cognito_logout_url(settings)}
