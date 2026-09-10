"""Judge examples must remain feasible after the original hackathon week."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from ordertowork.db import get_engine
from ordertowork.models.core import Workspace
from ordertowork.models.domain import Order, OrderRevision, SourceMessage
from ordertowork.services import profiles
from ordertowork.services.orders import availability
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.mark.parametrize("profile", ["merchandise", "bakery"])
@pytest.mark.parametrize(
    "now", [datetime(2026, 10, 8, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC)]
)
def test_guest_examples_keep_future_dates_and_feasible_alternatives(
    integrated, monkeypatch, profile, now
):
    monkeypatch.setattr(profiles, "utcnow", lambda: now)
    with Session(get_engine()) as db:
        workspace = Workspace(name="Guest example", profile=profile, is_demo=True)
        db.add(workspace)
        db.flush()
        profiles.seed_workspace(db, workspace, relative_dates=True)
        db.flush()
        order = db.scalar(select(Order).where(Order.workspace_id == workspace.id))
        revisions = list(
            db.scalars(select(OrderRevision).where(OrderRevision.order_id == order.id))
        )
        baseline = db.get(OrderRevision, order.accepted_revision_id)
        pickup = datetime.fromisoformat(baseline.terms["pickup_at"])
        assert pickup.date() >= now.astimezone(ZoneInfo(workspace.timezone)).date() + timedelta(
            days=5
        )
        assert not availability(db, order, baseline.resource_requirements)[0]
        alternatives = [revision for revision in revisions if revision.status == "proposed"]
        assert any(revision.feasible for revision in alternatives)
        requested = next(revision for revision in alternatives if not revision.feasible)
        requested_pickup = datetime.fromisoformat(requested.terms["pickup_at"])
        assert requested_pickup.weekday() == (4 if profile == "bakery" else 3)
        message = db.scalar(select(SourceMessage).where(SourceMessage.order_id == order.id))
        assert requested_pickup.date().isoformat() in message.body
        assert requested_pickup.strftime("%A") in message.body
        assert baseline.terms["rush_fee_cents"] == 0
        assert order.latest_analysis["evidence"][0]["source"] == "Synthetic demo fixture"
