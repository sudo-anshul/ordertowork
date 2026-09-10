import base64
import hashlib
import ipaddress
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, Response
from ordertowork.config import Settings, get_settings
from ordertowork.db import get_db, utcnow
from ordertowork.models.auth import AuthAuditEvent, AuthSession
from ordertowork.models.core import Membership, User, Workspace
from ordertowork.services.demo import demo_workspace_active
from sqlalchemy import select
from sqlalchemy.orm import Session

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def fail(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def normalize_email(value: str) -> str:
    value = value.strip().casefold()
    if len(value) > 320 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("Enter a valid email address")
    return value


def is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def development_enabled(settings: Settings) -> bool:
    return (
        settings.environment == "development"
        and settings.auth_mode == "development"
        and is_loopback(urlsplit(settings.app_url).hostname)
    )


def check_development_request(request: Request, settings: Settings) -> None:
    if not (
        development_enabled(settings)
        and is_loopback(request.url.hostname)
        and request.client is not None
        and is_loopback(request.client.host)
    ):
        raise fail(404, "development_login_disabled", "Development login is unavailable")
    check_request_origin(request, settings)


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    port = parts.port
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    suffix = (
        "" if port is None or (parts.scheme, port) in {("https", 443), ("http", 80)} else f":{port}"
    )
    return f"{parts.scheme}://{host}{suffix}"


def check_request_origin(request: Request, settings: Settings) -> None:
    origin = request.headers.get("origin")
    if origin is not None:
        try:
            valid = origin_of(origin) == origin_of(settings.app_url)
        except ValueError:
            valid = False
        if not valid or origin == "null":
            raise fail(
                403, "invalid_origin", "This request did not originate from this application"
            )
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise fail(403, "invalid_origin", "Cross-site requests are not permitted")


def platform_admin(user: User, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    allowed = {item for item in re.split(r"[\s,]+", settings.platform_admin_subjects) if item}
    if user.provider_subject.startswith("demo:"):
        return False
    if user.provider_subject.startswith("development:") and not development_enabled(settings):
        return False
    return user.provider_subject in allowed


@dataclass
class Actor:
    user: User
    csrf_token: str
    session: AuthSession | None = None


def is_demo_actor(actor: Actor) -> bool:
    return bool(
        (actor.session is not None and actor.session.auth_method == "demo")
        or actor.user.provider_subject.startswith("demo:")
    )


def require_business_actor(actor: Actor) -> None:
    if is_demo_actor(actor):
        raise fail(
            403,
            "demo_restricted",
            "This action is available in a business account. Explore the sample orders in your demo.",
        )


def request_session(request: Request, db: Session) -> AuthSession | None:
    """Only accept current configured sessions, including explicitly enabled guest sessions."""
    settings = get_settings()
    raw = request.cookies.get(settings.session_cookie, "")
    if not 32 <= len(raw) <= 256:
        return None
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest(raw)))
    if session is None or session.revoked_at or aware(session.expires_at) <= utcnow():
        return None
    if session.auth_method == "demo":
        return session if settings.demo_enabled else None
    return session if session.auth_method == settings.auth_mode else None


def get_actor(request: Request, db: Session = Depends(get_db)) -> Actor:
    settings = get_settings()
    session = request_session(request, db)
    if session is None:
        raise fail(401, "session_expired", "Your session expired. Sign in again")
    if session.auth_method == "development":
        check_development_request(request, settings)
    user = db.get(User, session.user_id)
    if user is None:
        raise fail(401, "authentication_required", "Sign in to continue")
    if request.method not in SAFE_METHODS:
        check_request_origin(request, settings)
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not secrets.compare_digest(supplied, session.csrf_token):
            raise fail(403, "invalid_csrf_token", "Refresh the page and try again")
    return Actor(user=user, csrf_token=session.csrf_token, session=session)


def require_membership(
    db: Session,
    actor: Actor,
    workspace_id: str,
    roles: tuple[str, ...] = ("owner", "operator"),
) -> Membership:
    membership = db.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == actor.user.id
        )
    )
    if membership is None:
        raise fail(404, "workspace_not_found", "Workspace not found")
    if membership.role not in roles:
        raise fail(403, "owner_required", "A workspace owner must perform this action")
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise fail(404, "workspace_not_found", "Workspace not found")
    if workspace.status != "active":
        raise fail(409, "workspace_archived", "This workspace is archived")
    if not demo_workspace_active(workspace):
        raise fail(410, "demo_expired", "This demo has ended. Start a new demo to explore again.")
    return membership


def audit(
    db: Session,
    action: str,
    user_id: str | None,
    workspace_id: str | None = None,
    details: dict | None = None,
) -> None:
    db.add(
        AuthAuditEvent(
            action=action, user_id=user_id, workspace_id=workspace_id, details=details or {}
        )
    )


def issue_session(
    db: Session,
    user: User,
    auth_method: str,
    response: Response,
    *,
    expires_at: datetime | None = None,
) -> AuthSession:
    settings = get_settings()
    if not 1 <= settings.session_hours <= 168:
        raise fail(
            503, "authentication_misconfigured", "Authentication is not configured correctly"
        )
    raw = secrets.token_urlsafe(48)
    expires_at = expires_at or (utcnow() + timedelta(hours=settings.session_hours))
    session = AuthSession(
        user_id=user.id,
        token_hash=digest(raw),
        csrf_token=secrets.token_urlsafe(32),
        auth_method=auth_method,
        expires_at=expires_at,
    )
    db.add(session)
    response.set_cookie(
        settings.session_cookie,
        raw,
        max_age=max(1, int((expires_at - utcnow()).total_seconds())),
        secure=urlsplit(settings.app_url).scheme == "https",
        httponly=True,
        samesite="lax",
        path="/",
    )
    return session


def workspace_dict(workspace: Workspace, role: str) -> dict:
    return {
        "id": workspace.id,
        "name": workspace.name,
        "profile": workspace.profile,
        "currency": workspace.currency,
        "timezone": workspace.timezone,
        "deposit_bps": workspace.deposit_bps,
        "role": role,
        "status": workspace.status,
        "is_demo": workspace.is_demo,
        "demo_expires_at": aware(workspace.demo_expires_at).isoformat()
        if workspace.demo_expires_at
        else None,
    }


def actor_payload(db: Session, actor: Actor) -> dict:
    rows = db.execute(
        select(Workspace, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == actor.user.id)
        .order_by(Workspace.created_at)
    ).all()
    user = actor.user
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "email_verified": user.email_verified,
            "is_platform_admin": not is_demo_actor(actor) and platform_admin(user),
        },
        "workspaces": [
            workspace_dict(workspace, role)
            for workspace, role in rows
            if demo_workspace_active(workspace)
        ],
        "csrf_token": actor.csrf_token,
        "auth_method": actor.session.auth_method if actor.session else get_settings().auth_mode,
        "demo": {
            "expires_at": aware(actor.session.expires_at).isoformat(),
            "max_agent_jobs_per_workspace": get_settings().max_demo_agent_jobs,
        }
        if actor.session is not None and is_demo_actor(actor)
        else None,
    }


def cognito_configuration(settings: Settings) -> tuple[str, str, str]:
    domain = settings.cognito_domain.rstrip("/")
    if domain and "://" not in domain:
        domain = "https://" + domain
    parts = urlsplit(domain)
    if not (
        settings.auth_mode == "cognito"
        and settings.cognito_region
        and settings.cognito_user_pool_id
        and settings.cognito_client_id
        and parts.scheme == "https"
        and parts.hostname
        and not parts.path
        and not parts.query
        and not parts.fragment
        and not parts.username
        and not parts.password
        and re.fullmatch(r"[a-z0-9-]+", settings.cognito_region)
        and re.fullmatch(r"[a-z0-9-]+_[A-Za-z0-9]+", settings.cognito_user_pool_id)
    ):
        raise fail(503, "authentication_misconfigured", "Cognito login has not been configured")
    issuer = (
        f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/"
        f"{settings.cognito_user_pool_id}"
    )
    redirect_uri = settings.app_url.rstrip("/") + "/api/auth/callback"
    return domain, issuer, redirect_uri


def pkce_challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode()
        .rstrip("=")
    )


@lru_cache(maxsize=4)
def jwks_client(issuer: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        issuer + "/.well-known/jwks.json", cache_jwk_set=True, lifespan=300, timeout=10
    )


async def exchange_code(code: str, verifier: str, settings: Settings) -> dict:
    domain, _, redirect_uri = cognito_configuration(settings)
    data = {
        "grant_type": "authorization_code",
        "client_id": settings.cognito_client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    auth = (
        httpx.BasicAuth(settings.cognito_client_id, settings.cognito_client_secret)
        if settings.cognito_client_secret
        else None
    )
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            result = await client.post(domain + "/oauth2/token", data=data, auth=auth)
            result.raise_for_status()
            payload = result.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("id_token"), str):
            raise ValueError("Missing ID token")
        return payload
    except (httpx.HTTPError, ValueError) as exc:
        raise fail(401, "login_failed", "Login could not be completed. Please try again") from exc


def validate_id_token(token: str, nonce_hash: str, settings: Settings) -> dict:
    _, issuer, _ = cognito_configuration(settings)
    try:
        key = jwks_client(issuer).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=settings.cognito_client_id,
            issuer=issuer,
            leeway=30,
            options={
                "require": ["exp", "iat", "iss", "aud", "sub", "nonce", "token_use"],
                "strict_aud": True,
            },
        )
        if claims["token_use"] != "id" or not secrets.compare_digest(
            digest(str(claims["nonce"])), nonce_hash
        ):
            raise ValueError("Invalid token purpose or nonce")
        if claims.get("email_verified") is not True:
            raise fail(
                403, "verified_email_required", "Verify your email in Cognito before signing in"
            )
        claims["email"] = normalize_email(claims.get("email", ""))
        if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 255:
            raise ValueError("Invalid subject")
        return claims
    except HTTPException:
        raise
    except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
        raise fail(
            401, "invalid_identity_token", "Login could not be verified. Please try again"
        ) from exc


def cognito_logout_url(settings: Settings) -> str | None:
    if settings.auth_mode != "cognito":
        return None
    domain, _, _ = cognito_configuration(settings)
    return (
        domain
        + "/logout?"
        + urlencode(
            {
                "client_id": settings.cognito_client_id,
                "logout_uri": settings.app_url.rstrip("/") + "/",
            }
        )
    )
