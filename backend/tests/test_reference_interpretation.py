"""Reference mode must decline ambiguity instead of silently dropping instructions."""

import pytest
from ordertowork.services.agent import RequestInterpretation, base_terms, reference_interpretation
from pydantic import ValidationError


@pytest.fixture
def merch_snapshot():
    return {
        "accepted_revision": {
            "terms": {
                "product_id": "shirts",
                "quantity": 30,
                "variant": "navy",
                "sizes": {"S": 6, "M": 12, "L": 12},
                "pickup_at": "2026-09-18T16:00:00-04:00",
                "specification": {"proof": "approved-v1", "placement": "Front", "ink_colors": "1"},
            }
        },
        "products": [{"id": "shirts", "variants": ["navy", "charcoal"]}],
        "resources": [
            {"key": "capacity:2026-09-17"},
            {"key": "capacity:2026-09-18"},
            {"key": "capacity:2026-09-24"},
            {"key": "capacity:2026-09-25"},
        ],
        "workspace": {"timezone": "America/New_York"},
    }


@pytest.mark.parametrize(
    "message",
    [
        "Do not add 15 medium shirts.",
        "Add 15 medium shirts and make it 60 total.",
        "Add 5 small and 10 medium shirts.",
        "Add 5 medium and add 10 medium shirts.",
        "Switch from navy to charcoal and add 5 medium shirts.",
        "Could we add 15 medium shirts and collect next Friday at 3 pm?",
        "Could we add 15 medium shirts and collect Thursday or Friday at 3 pm?",
        "Could we add 15 medium shirts and collect on 2026-09-17 at 15 pm?",
        "Could we add 15 medium shirts and collect on 2026-09-17 at 3:70 pm?",
        "Could we add 15 medium shirts and collect on 2026-99-17 at 3 pm?",
        "Could we add 15 medium shirts and collect Friday 2026-09-17 at 3 pm?",
        "Could we add 15 medium shirts and collect Thursday afternoon?",
        "Could we add 15 medium shirts and collect Thursday at noon or 3 pm?",
        "Could we add 15 medium shirts and collect at 3 pm?",
        "Could we add 15 medium shirts with a different artwork design?",
        "Could we add 15 medium shirts and change the placement to Back?",
    ],
)
def test_ambiguous_reference_requests_require_review(merch_snapshot, message):
    result = reference_interpretation(merch_snapshot, message)
    assert result.missing_fields, (message, result)
    assert result.evidence[0].quote in message


def test_supported_merchandise_example_preserves_exact_sizes(merch_snapshot):
    result = reference_interpretation(
        merch_snapshot,
        "Could we add 15 medium shirts and collect on Thursday at noon instead? Navy if possible. Please tell me the price difference before I confirm.",
    )
    assert result.intent == "change_request"
    assert not result.missing_fields
    assert result.quantity == 45
    assert result.sizes == {"S": 6, "M": 27, "L": 12}
    assert result.pickup_at == "2026-09-17T12:00:00-04:00"


def test_bakery_specification_changes_are_not_silently_ignored():
    snapshot = {
        "accepted_revision": {
            "terms": {
                "product_id": "cupcakes",
                "quantity": 24,
                "variant": "vanilla",
                "sizes": {},
                "pickup_at": "2026-09-19T10:00:00-04:00",
                "specification": {"icing": "Blue", "recipe": "V1"},
            }
        },
        "products": [{"id": "cupcakes", "variants": ["vanilla"]}],
        "resources": [{"key": "capacity:2026-09-18"}],
        "workspace": {"timezone": "America/New_York"},
    }
    supported = reference_interpretation(
        snapshot,
        "Could we make it 36 cupcakes, with the same vanilla flavor and blue icing, and collect Friday at 3 pm instead?",
    )
    assert not supported.missing_fields
    assert supported.quantity == 36
    for message in (
        "Make it 36 cupcakes with pink icing.",
        "Make it 36 cupcakes with chocolate flavor.",
        "Make it 36 cupcakes that are dairy free.",
    ):
        assert reference_interpretation(snapshot, message).missing_fields


def test_approval_is_intent_only_and_quantities_are_strict(merch_snapshot):
    assert reference_interpretation(merch_snapshot, "Approved").intent == "approval"
    with pytest.raises(ValidationError):
        RequestInterpretation(intent="change_request", quantity=True)
    with pytest.raises(ValidationError):
        RequestInterpretation(intent="change_request", sizes={"M": True})


def test_unaccepted_alternatives_do_not_replace_initial_baseline():
    snapshot = {
        "accepted_revision": None,
        "revisions": [
            {"number": 3, "terms": {"quantity": 99}},
            {"number": 1, "terms": {"quantity": 10}},
            {"number": 2, "terms": {"quantity": 20}},
        ],
    }
    assert base_terms(snapshot)["quantity"] == 10
