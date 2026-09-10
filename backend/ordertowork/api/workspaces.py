from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Response
from ordertowork.config import get_settings
from ordertowork.db import get_db, utcnow
from ordertowork.models.auth import AuthAuditEvent, AuthSession
from ordertowork.models.core import Membership, User, Workspace
from ordertowork.services.auth import (
    Actor,
    actor_payload,
    audit,
    development_enabled,
    fail,
    get_actor,
    normalize_email,
    platform_admin,
    require_membership,
    workspace_dict,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])
platform_router = APIRouter(prefix="/api/platform", tags=["platform administration"])


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def valid_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Choose a valid IANA timezone, such as Asia/Kolkata") from exc
    return value


class WorkspaceCreate(StrictInput):
    name: str = Field(min_length=1, max_length=120)
    profile: Literal["merchandise", "bakery"]
    currency: Literal["USD"] = "USD"
    timezone: str = Field(default="America/New_York", max_length=80)
    deposit_bps: int = Field(default=5000, ge=0, le=10000, strict=True)
    seed_demo: bool = False

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, value: str) -> str:
        return valid_timezone(value)


class WorkspacePatch(StrictInput):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, max_length=80)
    deposit_bps: int | None = Field(default=None, ge=0, le=10000, strict=True)

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, value: str | None) -> str | None:
        return valid_timezone(value) if value is not None else None


class MemberCreate(StrictInput):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["owner", "operator"] = "operator"

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str) -> str:
        return normalize_email(value)


class MemberPatch(StrictInput):
    role: Literal["owner", "operator"]


class WorkspaceArchive(StrictInput):
    confirm_name: str = Field(min_length=1, max_length=120)


def member_dict(member: Membership, user: User) -> dict:
    return {
        "id": member.id,
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "role": member.role,
        "created_at": member.created_at.isoformat(),
    }


def lock_workspace(db: Session, workspace_id: str) -> Workspace:
    workspace = db.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update())
    if workspace is None:
        raise fail(404, "workspace_not_found", "Workspace not found")
    return workspace


@router.get("")
def list_workspaces(actor: Actor = Depends(get_actor), db: Session = Depends(get_db)) -> dict:
    return {"workspaces": actor_payload(db, actor)["workspaces"]}


@router.post("", status_code=201)
def create_workspace(
    body: WorkspaceCreate,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    from ordertowork.services.profiles import configure_workspace, seed_workspace

    workspace = Workspace(
        name=body.name,
        profile=body.profile,
        currency=body.currency,
        timezone=body.timezone,
        deposit_bps=body.deposit_bps,
        is_demo=body.seed_demo,
    )
    db.add(workspace)
    db.flush()
    member = Membership(workspace_id=workspace.id, user_id=actor.user.id, role="owner")
    db.add(member)
    if body.seed_demo:
        seed_workspace(db, workspace)
    else:
        configure_workspace(db, workspace)
    audit(db, "workspace.created", actor.user.id, workspace.id, {"seed_demo": body.seed_demo})
    db.commit()
    return workspace_dict(workspace, "owner")


@router.get("/{workspace_id}")
def read_workspace(
    workspace_id: str,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    member = require_membership(db, actor, workspace_id)
    return workspace_dict(db.get(Workspace, workspace_id), member.role)


@router.patch("/{workspace_id}")
def patch_workspace(
    workspace_id: str,
    body: WorkspacePatch,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    workspace = lock_workspace(db, workspace_id)
    require_membership(db, actor, workspace_id, roles=("owner",))
    changes = body.model_dump(exclude_unset=True)
    if not changes or any(value is None for value in changes.values()):
        raise fail(422, "invalid_settings", "Provide at least one non-null setting")
    for key, value in changes.items():
        setattr(workspace, key, value)
    audit(db, "workspace.settings_changed", actor.user.id, workspace.id, {"fields": list(changes)})
    db.commit()
    return workspace_dict(workspace, "owner")


@router.delete("/{workspace_id}")
def archive_workspace(
    workspace_id: str,
    body: WorkspaceArchive,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    workspace = lock_workspace(db, workspace_id)
    require_membership(db, actor, workspace_id, roles=("owner",))
    if body.confirm_name != workspace.name:
        raise fail(422, "name_confirmation_required", "Enter the workspace name to archive it")
    workspace.status = "archived"
    audit(db, "workspace.archived", actor.user.id, workspace_id)
    db.commit()
    return {"ok": True, "status": "archived"}


@router.get("/{workspace_id}/members")
def list_members(
    workspace_id: str,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    require_membership(db, actor, workspace_id)
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.workspace_id == workspace_id)
        .order_by(Membership.created_at)
    ).all()
    return {"members": [member_dict(member, user) for member, user in rows]}


@router.post("/{workspace_id}/members", status_code=201)
def add_member(
    workspace_id: str,
    body: MemberCreate,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    lock_workspace(db, workspace_id)
    require_membership(db, actor, workspace_id, roles=("owner",))
    users = list(db.scalars(select(User).where(User.email == body.email)))
    local = development_enabled(get_settings())
    eligible = [
        user
        for user in users
        if user.email_verified or (local and user.provider_subject.startswith("development:"))
    ]
    if len(eligible) != 1:
        raise fail(
            422,
            "verified_account_required",
            "Ask this person to sign in and verify their email first",
        )
    user = eligible[0]
    if db.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == user.id
        )
    ):
        raise fail(409, "already_a_member", "This person already belongs to this workspace")
    member = Membership(workspace_id=workspace_id, user_id=user.id, role=body.role)
    db.add(member)
    audit(
        db, "membership.added", actor.user.id, workspace_id, {"user_id": user.id, "role": body.role}
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise fail(
            409, "already_a_member", "This person already belongs to this workspace"
        ) from exc
    return member_dict(member, user)


def target_member(db: Session, workspace_id: str, membership_id: str) -> Membership:
    member = db.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.id == membership_id
        )
    )
    if member is None:
        raise fail(404, "member_not_found", "Workspace member not found")
    return member


def protect_final_owner(db: Session, member: Membership) -> None:
    if member.role != "owner":
        return
    count = db.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.workspace_id == member.workspace_id, Membership.role == "owner")
    )
    if count <= 1:
        raise fail(409, "final_owner_required", "A workspace must retain at least one owner")


@router.patch("/{workspace_id}/members/{membership_id}")
def change_member_role(
    workspace_id: str,
    membership_id: str,
    body: MemberPatch,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    lock_workspace(db, workspace_id)
    require_membership(db, actor, workspace_id, roles=("owner",))
    member = target_member(db, workspace_id, membership_id)
    if body.role != "owner":
        protect_final_owner(db, member)
    member.role = body.role
    audit(
        db,
        "membership.role_changed",
        actor.user.id,
        workspace_id,
        {"user_id": member.user_id, "role": body.role},
    )
    db.commit()
    return member_dict(member, db.get(User, member.user_id))


@router.delete("/{workspace_id}/members/{membership_id}")
def remove_member(
    workspace_id: str,
    membership_id: str,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    lock_workspace(db, workspace_id)
    require_membership(db, actor, workspace_id, roles=("owner",))
    member = target_member(db, workspace_id, membership_id)
    protect_final_owner(db, member)
    audit(db, "membership.removed", actor.user.id, workspace_id, {"user_id": member.user_id})
    db.delete(member)
    db.commit()
    return {"ok": True}


@platform_router.get("/overview")
def platform_overview(
    response: Response,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> dict:
    if not platform_admin(actor.user):
        raise fail(403, "platform_admin_required", "Platform support access is required")
    response.headers["Cache-Control"] = "no-store"
    counts = {
        "users": db.scalar(select(func.count()).select_from(User)),
        "workspaces": db.scalar(select(func.count()).select_from(Workspace)),
        "memberships": db.scalar(select(func.count()).select_from(Membership)),
        "active_sessions": db.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow())
        ),
    }
    workspaces = db.scalars(select(Workspace).order_by(Workspace.created_at.desc()).limit(100))
    events = db.scalars(select(AuthAuditEvent).order_by(AuthAuditEvent.created_at.desc()).limit(50))
    return {
        "counts": counts,
        "workspaces": [
            {
                "id": workspace.id,
                "name": workspace.name,
                "profile": workspace.profile,
                "status": workspace.status,
                "created_at": workspace.created_at.isoformat(),
            }
            for workspace in workspaces
        ],
        "recent_audit": [
            {
                "id": event.id,
                "action": event.action,
                "user_id": event.user_id,
                "workspace_id": event.workspace_id,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ],
    }
