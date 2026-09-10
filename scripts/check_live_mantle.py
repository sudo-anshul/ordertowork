"""Opt-in hosted check: two synthetic guest analyses, then approval through production.

No network requests are made without --run-paid-check. Reports deliberately omit
cookies, CSRF tokens, customer links, source messages, order IDs and raw errors.
"""

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

MODEL_ID = "qwen.qwen3-235b-a22b-2507"
EXPECTED_QUANTITIES = {"merchandise": 45, "bakery": 36}
POLL_SECONDS = 120


class CheckFailed(Exception):
    """Only fixed, nonsecret check labels may be used as exception messages."""


def require(condition, label):
    if not condition:
        raise CheckFailed(label)


def payload(response, expected=200):
    # Do not include response bodies, request URLs or server errors in the report.
    require(response.status_code == expected, f"unexpected_http_status_{response.status_code}")
    result = response.json()
    require(isinstance(result, dict), "response_is_not_an_object")
    return result


def origin_url(value):
    parsed = urlsplit(value)
    require(
        parsed.scheme in ("http", "https")
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment,
        "url_must_be_an_origin_without_credentials_path_query_or_fragment",
    )
    require(
        parsed.scheme == "https" or parsed.hostname in ("localhost", "127.0.0.1", "::1"),
        "use_https_except_for_loopback",
    )
    return f"{parsed.scheme}://{parsed.netloc}"


def poll_job(client, path):
    deadline = time.monotonic() + POLL_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        require(remaining > 0, "analysis_poll_timeout")
        job = payload(client.get(path, timeout=min(15, remaining)))
        if job.get("status") in ("succeeded", "failed"):
            return job
        require(job.get("status") in ("queued", "running"), "unexpected_job_status")
        time.sleep(min(2, max(0, deadline - time.monotonic())))


def check_analysis(visitor, workspace, detail, report):
    profile = workspace["profile"]
    expected_quantity = EXPECTED_QUANTITIES[profile]
    prefix = f"/api/workspaces/{workspace['id']}"
    path = f"{prefix}/orders/{detail['id']}"
    baseline = detail["accepted_revision"]
    old_revision_ids = {revision["id"] for revision in detail["revisions"]}
    check = {"profile": profile, "status": "running", "checks": []}
    report["businesses"].append(check)
    report["analysis_submissions"] += 1  # Count even an ambiguous network/HTTP failure.
    require(report["analysis_submissions"] <= 2, "analysis_submission_limit")
    submitted = payload(
        visitor.post(
            path + "/messages",
            json={"body": detail["messages"][-1]["body"], "source": "manual"},
        ),
        202,
    )
    job = poll_job(visitor, f"{prefix}/jobs/{submitted['job']['id']}")
    check["job_status"] = job.get("status")
    require(job.get("status") == "succeeded", "analysis_did_not_succeed")
    require(job.get("mode") == "bedrock", "analysis_was_not_bedrock")
    require(job.get("attempts") == 1, "unexpected_analysis_retry")
    require(job.get("message_id") == submitted["message"]["id"], "analysis_message_mismatch")
    events = job.get("tool_events", [])
    usage = [event.get("result", {}) for event in events if event.get("tool") == "bedrock_usage"]
    require(len(usage) == 1, "expected_one_usage_record")
    usage = usage[0]
    require(usage.get("endpoint") == "mantle", "analysis_endpoint_was_not_mantle")
    require(usage.get("model_id") == MODEL_ID, "analysis_model_mismatch")
    require(usage.get("usage_complete") is True, "incomplete_usage_record")
    for key in ("input_tokens", "output_tokens", "total_tokens", "model_calls"):
        value = usage.get(key)
        require(type(value) is int and value > 0, "missing_positive_usage")
    require(
        any(
            event.get("tool") == "read_order_context"
            and event.get("result", {}).get("source") == "database"
            and event.get("result", {}).get("order_id") == detail["id"]
            for event in events
        ),
        "no_database_context_tool_for_order",
    )
    previews = [
        event.get("result", {}) for event in events if event.get("tool") == "preview_change"
    ]
    require(
        any(preview.get("terms", {}).get("quantity") == expected_quantity for preview in previews),
        "requested_quantity_not_previewed",
    )
    check["usage"] = {
        key: usage[key]
        for key in (
            "model_id",
            "endpoint",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "model_calls",
        )
    }
    check["checks"].append("live_mantle_model_and_tools")
    updated = payload(visitor.get(path))
    require(updated.get("is_demo") is True, "order_no_longer_synthetic")
    require(updated.get("latest_job", {}).get("id") == job["id"], "latest_job_mismatch")
    require(updated.get("accepted_revision") == baseline, "analysis_changed_accepted_revision")
    check["checks"].append("accepted_revision_unchanged")
    analysis = updated.get("latest_analysis") or {}
    missing = analysis.get("missing_fields")
    require(isinstance(missing, list), "analysis_missing_details_not_recorded")
    check["missing_details_count"] = len(missing)
    require(not missing, "analysis_requires_clarification")
    require(analysis.get("intent") == "change_request", "analysis_intent_mismatch")
    new_proposals = [
        revision
        for revision in updated["revisions"]
        if revision["id"] not in old_revision_ids and revision["status"] == "proposed"
    ]
    require(bool(new_proposals), "no_new_analysis_proposals")
    requested = min(new_proposals, key=lambda revision: revision["number"])
    require(requested["terms"]["quantity"] == expected_quantity, "requested_quantity_mismatch")
    feasible = [revision for revision in new_proposals if revision.get("feasible") is True]
    require(bool(feasible), "no_new_feasible_proposal")
    require(
        all(revision["base_accepted_revision_id"] == baseline["id"] for revision in new_proposals),
        "proposal_baseline_mismatch",
    )
    check["requested_quantity"] = expected_quantity
    check["new_feasible_proposals"] = len(feasible)
    check["checks"].append("new_requested_and_feasible_proposals")
    return path, min(feasible, key=lambda revision: revision["number"]), check


def complete_workflow(visitor, customer, origin, path, revision, check):
    # All mutations below are solely against the already-validated synthetic order.
    shared = payload(visitor.post(path + f"/proposals/{revision['id']}/share"))
    link = urlsplit(shared["url"])
    require(
        f"{link.scheme}://{link.netloc}" == origin
        and link.path.startswith("/customer/")
        and not link.query
        and not link.fragment,
        "unexpected_customer_link_origin_or_path",
    )
    customer_path = "/api" + link.path
    view = payload(customer.get(customer_path))
    require(view.get("order", {}).get("is_demo") is True, "customer_order_is_not_synthetic")
    require(view["revision"]["id"] == revision["id"], "customer_revision_mismatch")
    require(view["revision"]["terms_hash"] == revision["terms_hash"], "customer_terms_mismatch")
    approved = payload(
        customer.post(
            customer_path + "/approve",
            json={"terms_hash": view["revision"]["terms_hash"], "consent": True},
        )
    )
    require(approved.get("status") == "approved", "customer_approval_failed")
    check["checks"].append("exact_revision_customer_approval")
    updated = payload(visitor.get(path))
    require(updated.get("is_demo") is True, "deposit_order_is_not_synthetic")
    require(updated["accepted_revision"]["id"] == revision["id"], "approved_revision_not_accepted")
    due = max(0, revision["terms"]["required_deposit_cents"] - updated["deposit_paid_cents"])
    if due:
        payload(
            visitor.post(
                path + "/deposits",
                json={
                    "amount_cents": due,
                    "reference": "SAMPLE live Mantle verification; no real payment",
                    "idempotency_key": "live-mantle-smoke-sample-deposit",
                },
            )
        )
    check["sample_deposit_recorded"] = bool(due)
    ticket = payload(visitor.get(path + "/ticket"))
    require(ticket.get("is_demo") is True, "ticket_is_not_synthetic")
    require(ticket["revision"] == revision["number"], "ticket_revision_mismatch")
    require(ticket["terms"] == revision["terms"], "ticket_terms_mismatch")
    payload(
        visitor.post(path + "/production/start", json={"expected_revision": ticket["revision"]})
    )
    final = payload(visitor.get(path))
    require(final.get("production_status") == "started", "production_did_not_start")
    require(final["accepted_revision"]["id"] == revision["id"], "production_revision_mismatch")
    check["checks"].append("sample_deposit_ticket_and_production")
    check["status"] = "passed"


def run(origin, report):
    # Fresh in-memory clients: no business cookies, redirect following or request retries.
    with (
        httpx.Client(base_url=origin, timeout=20, headers={"Origin": origin}) as visitor,
        httpx.Client(base_url=origin, timeout=20, headers={"Origin": origin}) as customer,
    ):
        session_started = False
        try:
            config = payload(visitor.get("/api/auth/config"))
            require(config.get("demo_enabled") is True, "guest_demo_disabled")
            session = payload(visitor.post("/api/auth/demo"))
            session_started = True
            visitor.headers["X-CSRF-Token"] = session["csrf_token"]
            require(session.get("auth_method") == "demo", "session_is_not_a_guest")
            require(bool(session.get("demo", {}).get("expires_at")), "guest_session_has_no_expiry")
            require(session["user"].get("is_platform_admin") is False, "guest_has_admin_privilege")
            workspaces = session.get("workspaces", [])
            require(len(workspaces) == 2, "expected_two_guest_workspaces")
            require(
                {workspace["profile"] for workspace in workspaces} == set(EXPECTED_QUANTITIES),
                "unexpected_guest_business_profiles",
            )
            fixtures = []
            for workspace in workspaces:
                require(workspace.get("is_demo") is True, "workspace_is_not_synthetic")
                require(bool(workspace.get("demo_expires_at")), "workspace_has_no_guest_expiry")
                prefix = f"/api/workspaces/{workspace['id']}"
                orders = payload(visitor.get(prefix + "/orders"))["orders"]
                require(len(orders) == 1, "expected_one_seeded_order_per_business")
                require(orders[0].get("is_demo") is True, "listed_order_is_not_synthetic")
                detail = payload(visitor.get(prefix + "/orders/" + orders[0]["id"]))
                require(detail.get("is_demo") is True, "seeded_order_is_not_synthetic")
                require(bool(detail.get("accepted_revision")), "seeded_order_has_no_baseline")
                require(bool(detail.get("messages")), "seeded_order_has_no_message")
                require(detail.get("latest_job") is None, "guest_order_already_has_an_analysis_job")
                fixtures.append((workspace, detail))
            for workspace, detail in fixtures:
                path, revision, check = check_analysis(visitor, workspace, detail, report)
                complete_workflow(visitor, customer, origin, path, revision, check)
            report["status"] = "passed"
        finally:
            if session_started:
                try:
                    result = payload(visitor.post("/api/auth/logout"))
                    report["guest_logout_succeeded"] = result.get("logout_url") is None
                except Exception:
                    report["guest_logout_succeeded"] = False
                if report.get("status") == "passed":
                    require(report["guest_logout_succeeded"], "guest_logout_failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Server origin, for example https://demo.example.com")
    parser.add_argument(
        "--run-paid-check", action="store_true", help="Allow at most two paid analyses"
    )
    parser.add_argument("--output", type=Path, help="Optional sanitized JSON report path")
    args = parser.parse_args()
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "status": "plan",
        "analysis_submissions": 0,
        "businesses": [],
        "plan": [
            "Create one new isolated guest session; verify both workspaces and orders are synthetic.",
            "Submit each seeded message once; poll up to 120 seconds per job, with no retries.",
            "Require live Qwen via Mantle, positive usage, tools and unchanged accepted terms.",
            "Approve a new feasible proposal; record sample deposit; verify ticket and start work.",
            "Log out in finally. Never record credentials, customer links or raw API bodies.",
        ],
    }
    logging.disable(logging.CRITICAL)
    try:
        if args.url:
            report["origin"] = origin_url(args.url)
        if args.run_paid_check:
            require(bool(args.url), "url_required_for_paid_check")
            run(report["origin"], report)
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error) if isinstance(error, CheckFailed) else type(error).__name__
        for business in report["businesses"]:
            if business["status"] == "running":
                business["status"] = "failed"
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
