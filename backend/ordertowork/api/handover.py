"""Owner handover operations and narrowly scoped customer fulfillment links."""

from typing import Literal

from fastapi import APIRouter, Depends
from ordertowork.api.orders import DepositInput, StrictInput
from ordertowork.db import get_db
from ordertowork.services import handover as service
from ordertowork.services.auth import Actor, get_actor, require_membership
from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api", tags=["handover"])
PREFIX = "/workspaces/{workspace_id}/orders/{order_id}/handover"


class ReadyInput(StrictInput):
    expected_revision: int = Field(ge=1, strict=True)
    collection_address: str = Field(min_length=3, max_length=500)
    collection_instructions: str = Field(default="", max_length=1000)
    collection_window_start: AwareDatetime
    collection_window_end: AwareDatetime
    delivery_mode: Literal["unavailable", "included", "fixed", "quote"] = "unavailable"
    delivery_fee_cents: int = Field(default=0, ge=0, le=100000000, strict=True)
    delivery_area: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def consistent_delivery(self):
        if self.delivery_mode != "fixed" and self.delivery_fee_cents != 0:
            raise ValueError("Only fixed delivery has a configured additional fee")
        if self.delivery_mode != "unavailable" and not self.delivery_area:
            raise ValueError("Describe the delivery area the business can support")
        return self


class VersionInput(StrictInput):
    expected_version: int = Field(ge=1, strict=True)


class WindowInput(VersionInput):
    collection_window_start: AwareDatetime
    collection_window_end: AwareDatetime


class ChoiceInput(VersionInput):
    method: Literal["collection", "delivery"]
    collection_at: AwareDatetime | None = None
    delivery_address: str = Field(default="", max_length=1000)
    contact_phone: str = Field(default="", max_length=40)
    customer_note: str = Field(default="", max_length=1000)
    consent: bool = Field(strict=True)


class QuoteInput(VersionInput):
    delivery_fee_cents: int = Field(ge=0, le=100000000, strict=True)
    note: str = Field(default="", max_length=1000)


class QuoteAcceptance(VersionInput):
    quote_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    consent: bool = Field(strict=True)


def owner(db: Session, actor: Actor, workspace_id: str) -> None:
    require_membership(db, actor, workspace_id, roles=("owner",))


@router.get(PREFIX)
def read(
    workspace_id: str,
    order_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    return service.owner_view(db, workspace_id, order_id)


@router.post(PREFIX + "/ready")
def ready(
    workspace_id: str,
    order_id: str,
    body: ReadyInput,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    result = service.ready(db, workspace_id, order_id, body.model_dump())
    db.commit()
    return result


@router.post(PREFIX + "/share")
def share(
    workspace_id: str,
    order_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    result = service.share(db, workspace_id, order_id)
    db.commit()
    return result


@router.post(PREFIX + "/quote")
def quote(
    workspace_id: str,
    order_id: str,
    body: QuoteInput,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    result = service.quote(db, workspace_id, order_id, body.model_dump())
    db.commit()
    return result


@router.post(PREFIX + "/window")
def update_window(
    workspace_id: str,
    order_id: str,
    body: WindowInput,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    result = service.update_window(db, workspace_id, order_id, body.model_dump())
    db.commit()
    return result


@router.post(PREFIX + "/payment")
def payment(
    workspace_id: str,
    order_id: str,
    body: DepositInput,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    result = service.record_payment(db, workspace_id, order_id, body.model_dump(), actor.user.id)
    db.commit()
    return result


@router.post(PREFIX + "/dispatch")
def dispatch(
    workspace_id: str,
    order_id: str,
    body: VersionInput,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    result = service.transition(db, workspace_id, order_id, "dispatch", body.expected_version)
    db.commit()
    return result


@router.post(PREFIX + "/collect")
def collect(
    workspace_id: str,
    order_id: str,
    body: VersionInput,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    result = service.transition(db, workspace_id, order_id, "collect", body.expected_version)
    db.commit()
    return result


@router.post(PREFIX + "/deliver")
def deliver(
    workspace_id: str,
    order_id: str,
    body: VersionInput,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    owner(db, actor, workspace_id)
    result = service.transition(db, workspace_id, order_id, "deliver", body.expected_version)
    db.commit()
    return result


@router.get("/handover/{token}")
def customer(token: str, db: Session = Depends(get_db)):
    return service.customer_view(db, token)


@router.post("/handover/{token}/choose")
def choose(token: str, body: ChoiceInput, db: Session = Depends(get_db)):
    result = service.choose(db, token, body.model_dump())
    db.commit()
    return result


@router.post("/handover/{token}/accept-quote")
def accept(token: str, body: QuoteAcceptance, db: Session = Depends(get_db)):
    result = service.accept_quote(db, token, body.model_dump())
    db.commit()
    return result
