#!/usr/bin/env python3
"""Check real AWS providers using disposable local application data, never the live database.

S3 requests are live. Bedrock is opt-in and limited to two synthetic jobs. Cognito
hosted login is a separate browser check; localhost test identities are not Cognito.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

MODEL_IDS = ("us.amazon.nova-lite-v1:0", "amazon.nova-lite-v1:0")
PDF = b"%PDF-1.4\n% OrderToWork synthetic live-provider smoke fixture.\n%%EOF\n"
LOCAL_ORIGIN = "http://localhost:5173"


class CheckFailed(RuntimeError):
    pass


def require(condition: bool, check: str) -> None:
    if not condition:
        raise CheckFailed(check)


@dataclass
class Ledger:
    bucket: str
    model_jobs: int
    model_gate_open: bool = False
    workspace_ids: set[str] = field(default_factory=set)
    versions: set[tuple[str, str | None]] = field(default_factory=set)
    uncertain_keys: dict[str, int] = field(default_factory=dict)
    counters: dict[str, int] = field(
        default_factory=lambda: {"s3_puts": 0, "s3_gets": 0, "bedrock_calls": 0}
    )
    lock: threading.Lock = field(default_factory=threading.Lock)

    def own_key(self, key: str) -> bool:
        return any(
            key.startswith(f"attachments/{wid}/")
            or key.startswith(f"agent-sessions/session/{wid}-")
            for wid in self.workspace_ids
        )

    def check_key(self, kwargs: dict) -> str:
        key = kwargs.get("Key", "")
        require(kwargs.get("Bucket") == self.bucket and self.own_key(key), "s3_scope")
        return key


class TrackedS3:
    """Observe SDK calls without changing the application's storage implementation."""

    def __init__(self, client, ledger: Ledger):
        self.client, self.ledger = client, ledger

    def __getattr__(self, name):
        return getattr(self.client, name)

    def put_object(self, **kwargs):
        key = self.ledger.check_key(kwargs)
        with self.ledger.lock:
            self.ledger.uncertain_keys[key] = self.ledger.uncertain_keys.get(key, 0) + 1
            self.ledger.counters["s3_puts"] += 1
        response = self.client.put_object(**kwargs)
        with self.ledger.lock:
            self.ledger.versions.add((key, response.get("VersionId")))
            self.ledger.uncertain_keys[key] -= 1
            if self.ledger.uncertain_keys[key] == 0:
                del self.ledger.uncertain_keys[key]
        return response

    def get_object(self, **kwargs):
        self.ledger.check_key(kwargs)
        with self.ledger.lock:
            self.ledger.counters["s3_gets"] += 1
        return self.client.get_object(**kwargs)

    def delete_object(self, **kwargs):
        # An application rollback may delete a just-uploaded object. Record any
        # resulting delete marker so final cleanup can remove that exact version.
        key = self.ledger.check_key(kwargs)
        response = self.client.delete_object(**kwargs)
        if response.get("DeleteMarker") and response.get("VersionId"):
            self.ledger.versions.add((key, response["VersionId"]))
        return response

    def generate_presigned_url(self, operation, *, Params, **kwargs):
        require(operation == "get_object", "presign_operation")
        self.ledger.check_key(Params)
        return self.client.generate_presigned_url(operation, Params=Params, **kwargs)


class TrackedBedrock:
    def __init__(self, client, ledger: Ledger):
        self.client, self.ledger = client, ledger

    def __getattr__(self, name):
        return getattr(self.client, name)

    def call(self, operation, **kwargs):
        with self.ledger.lock:
            require(
                self.ledger.counters["bedrock_calls"] < self.ledger.model_jobs * 5,
                "model_request_budget",
            )
            require(kwargs.get("modelId") in MODEL_IDS, "low_cost_model_required")
            self.ledger.counters["bedrock_calls"] += 1
        return getattr(self.client, operation)(**kwargs)

    def converse_stream(self, **kwargs):
        return self.call("converse_stream", **kwargs)

    def converse(self, **kwargs):
        return self.call("converse", **kwargs)


@contextmanager
def isolated_runtime(args, directory: Path, ledger: Ledger):
    """Pin every setting and credential profile inside this short-lived process."""
    previous_env = dict(os.environ)
    previous_cwd = Path.cwd()
    previous_session = boto3.DEFAULT_SESSION
    previous_logging = logging.root.manager.disable
    original_client = boto3.session.Session.client
    for key in list(os.environ):
        if key.startswith("OTW_") or key in {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_SECURITY_TOKEN",
        }:
            os.environ.pop(key, None)
    os.environ.update(
        {
            "OTW_DATABASE_URL": f"sqlite:///{directory / 'smoke.db'}",
            "OTW_ENVIRONMENT": "development",
            "OTW_AUTH_MODE": "development",
            "OTW_APP_URL": LOCAL_ORIGIN,
            "OTW_SESSION_COOKIE": "otw_smoke_session",
            "OTW_STORAGE_MODE": "s3",
            "OTW_S3_BUCKET": args.bucket,
            "OTW_AWS_REGION": args.region,
            "OTW_AGENT_MODE": "bedrock",
            "OTW_BEDROCK_MODEL_ID": args.model_id,
            "OTW_MAX_DAILY_BEDROCK_ATTEMPTS": "0",
            "OTW_MAX_JOB_ATTEMPTS": "1",
            "OTW_BEDROCK_MAX_OUTPUT_TOKENS": "1024",
            "OTW_AGENT_MAX_TURNS": "5",
            "OTW_AGENT_MAX_TOTAL_TOKENS": "18000",
            "OTW_AGENT_TIMEOUT_SECONDS": "120",
            "OTW_WORKER_LEASE_SECONDS": "180",
            "OTW_DATA_DIR": str(directory / "data"),
            "AWS_PROFILE": args.profile,
            "AWS_DEFAULT_PROFILE": args.profile,
            "AWS_DEFAULT_REGION": args.region,
            "AWS_REGION": args.region,
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_IGNORE_CONFIGURED_ENDPOINT_URLS": "true",
        }
    )
    os.chdir(directory)  # No repository .env is read, including database credentials.
    logging.disable(logging.CRITICAL)  # Do not emit SDK URLs, tokens or provider bodies.

    def tracked_client(session, service_name, *positional, **kwargs):
        require(service_name in {"s3", "bedrock-runtime"}, "unexpected_aws_service")
        if service_name == "bedrock-runtime":
            require(ledger.model_jobs > 0 and ledger.model_gate_open, "model_not_requested")
        config = kwargs.get("config") or Config()
        kwargs["config"] = config.merge(
            # Retrying a versioned PUT after a lost response can leave an extra
            # unreported version. One S3 attempt makes uncertain writes explicit.
            Config(
                connect_timeout=5,
                read_timeout=30,
                retries={"total_max_attempts": 1 if service_name == "s3" else 2},
            )
        )
        client = original_client(session, service_name, *positional, **kwargs)
        return TrackedS3(client, ledger) if service_name == "s3" else TrackedBedrock(client, ledger)

    try:
        boto3.setup_default_session(profile_name=args.profile, region_name=args.region)
        with patch.object(boto3.session.Session, "client", tracked_client):
            from ordertowork.config import get_settings
            from ordertowork.db import Base, get_engine
            from ordertowork.main import create_app

            get_settings.cache_clear()
            get_engine.cache_clear()
            require(
                get_settings().database_url == f"sqlite:///{directory / 'smoke.db'}",
                "temporary_database_required",
            )
            Base.metadata.create_all(get_engine())
            try:
                yield create_app(), original_client
            finally:
                get_engine().dispose()
                get_engine.cache_clear()
                get_settings.cache_clear()
    finally:
        boto3.DEFAULT_SESSION = previous_session
        os.chdir(previous_cwd)
        os.environ.clear()
        os.environ.update(previous_env)
        logging.disable(previous_logging)


def fixture(client: TestClient, profile: str) -> dict:
    response = client.post(
        "/api/auth/development-login",
        json={"email": f"smoke-{profile}@example.com", "name": "Synthetic AWS smoke owner"},
    )
    require(response.status_code == 200, "local_fixture_login")
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    response = client.post(
        "/api/workspaces",
        json={"name": "Synthetic AWS smoke " + profile, "profile": profile, "seed_demo": True},
    )
    require(response.status_code == 201, "fixture_workspace")
    wid = response.json()["id"]
    orders = client.get(f"/api/workspaces/{wid}/orders").json()["orders"]
    require(len(orders) == 1, "fixture_order")
    oid = orders[0]["id"]
    path = f"/api/workspaces/{wid}/orders/{oid}"
    order = client.get(path).json()
    expected_date = "2026-09-18" if profile == "merchandise" else "2026-09-19"
    require(
        order["accepted_revision"]["terms"]["pickup_at"].startswith(expected_date), "fixture_date"
    )
    return {"client": client, "wid": wid, "path": path, "order": order, "profile": profile}


def check_storage(first: dict, second: dict, anonymous: TestClient, report: dict):
    client, path = first["client"], first["path"] + "/files"
    response = client.post(path, files={"file": ("synthetic-smoke.pdf", PDF, "application/pdf")})
    require(response.status_code == 201, "s3_upload")
    attachment = response.json()
    require(attachment["sha256"] == hashlib.sha256(PDF).hexdigest(), "s3_upload_digest")
    download = path + f"/{attachment['id']}/download"
    require(anonymous.get(download).status_code == 401, "anonymous_attachment_denied")
    require(second["client"].get(download).status_code == 404, "foreign_tenant_denied")
    wrong_order = second["path"] + f"/files/{attachment['id']}/download"
    require(second["client"].get(wrong_order).status_code == 404, "foreign_attachment_id_denied")
    response = client.get(download, follow_redirects=False)
    require(response.status_code == 303, "s3_presign_redirect")
    url = response.headers["location"]
    parsed = urlsplit(url)
    require(
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.hostname.endswith(".amazonaws.com")
        and parsed.port in (None, 443),
        "s3_https_destination",
    )
    with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as cloud:
        with cloud.stream("GET", url) as received:
            require(received.status_code == 200, "s3_signed_download")
            require(
                "attachment" in received.headers.get("content-disposition", ""),
                "s3_download_disposition",
            )
            content = bytearray()
            for chunk in received.iter_bytes(chunk_size=1024):
                content.extend(chunk)
                require(len(content) <= len(PDF), "s3_download_size")
            require(bytes(content) == PDF, "s3_download_bytes")
        unsigned = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        with cloud.stream("GET", unsigned) as denied:
            require(denied.status_code == 403, "s3_public_access_denied")
    report["checks"]["private_s3_round_trip"] = "passed"
    report["checks"]["local_tenant_attachment_boundaries"] = "passed"
    report["uploaded_bytes"] = len(PDF)


def check_budget(first: dict, ledger: Ledger, report: dict):
    from ordertowork.db import session_factory
    from ordertowork.models.jobs import Job
    from ordertowork.worker import process_one

    response = first["client"].post(
        first["path"] + "/messages", json={"body": "Budget gate probe; never send this to a model."}
    )
    require(response.status_code == 202, "budget_probe_enqueue")
    calls_before = dict(ledger.counters)
    require(asyncio.run(process_one()) is False, "budget_probe_not_claimed")
    with session_factory()() as db:
        job = db.get(Job, response.json()["job"]["id"])
        require(job.status == "failed" and job.attempts == 0, "budget_probe_blocked")
        require("budget reached" in job.error, "budget_probe_reason")
    require(calls_before == ledger.counters, "budget_probe_no_provider_call")
    report["checks"]["zero_budget_blocks_before_provider"] = "passed"


def check_models(fixtures: list[dict], report: dict):
    from ordertowork.config import get_settings
    from ordertowork.db import session_factory
    from ordertowork.models.jobs import Job
    from ordertowork.worker import process_one

    get_settings().max_daily_bedrock_attempts = len(fixtures)
    for item in fixtures:
        merch = item["profile"] == "merchandise"
        body = (
            "Please propose 45 navy shirts: 6 small, 27 medium and 12 large. "
            "Pickup is 2026-09-17 at noon America/New_York. Keep the accepted print "
            "specification. Send the revised price for review before I approve."
            if merch
            else "Please propose 36 vanilla cupcakes with the same blue icing and recipe V1. "
            "Pickup is 2026-09-18 at 3 pm America/New_York. "
            "Send the revised price for review before I approve."
        )
        response = item["client"].post(item["path"] + "/messages", json={"body": body})
        require(response.status_code == 202, "model_enqueue")
        report["model_jobs_attempted"] += 1
        require(asyncio.run(process_one()) is True, "model_job_claimed")
        with session_factory()() as db:
            job = db.get(Job, response.json()["job"]["id"])
            for event in job.tool_events:
                if event.get("tool") == "bedrock_usage":
                    usage = event["result"]
                    for name in ("input_tokens", "output_tokens", "total_tokens"):
                        report[name] += int(usage.get(name, 0))
                    report["usage_complete"] &= bool(usage.get("usage_complete"))
            require(job.status == "succeeded", "bedrock_job_incomplete")
            require(job.result["intent"] == "change_request", "model_request_intent")
            require(not job.result["missing_fields"], "model_request_needs_clarification")
            tools = {event.get("tool") for event in job.tool_events}
            require(
                {"read_order_context", "preview_change", "bedrock_usage"} <= tools,
                "strands_tools_recorded",
            )
        updated = item["client"].get(item["path"]).json()
        require(
            updated["accepted_revision"] == item["order"]["accepted_revision"],
            "accepted_order_preserved",
        )
        require(
            updated["deposit_paid_cents"] == item["order"]["deposit_paid_cents"],
            "deposit_preserved",
        )
        proposed = [r for r in updated["revisions"] if r["status"] == "proposed"]
        require(
            any(r["terms"]["quantity"] == (45 if merch else 36) for r in proposed),
            "requested_quantity_proposed",
        )
        report["model_jobs_passed"] += 1
    report["checks"]["live_strands_bedrock"] = "passed"


def cleanup(ledger: Ledger, args, original_client, report: dict):
    """Never enumerate a bucket or remove keys outside this run's recorded writes."""
    if not ledger.versions and not ledger.uncertain_keys:
        report["checks"]["created_objects_cleaned"] = "passed"
        return
    session = boto3.Session(
        profile_name=args.cleanup_profile or args.profile, region_name=args.region
    )
    client = original_client(
        session,
        "s3",
        config=Config(retries={"total_max_attempts": 2}, connect_timeout=5, read_timeout=20),
    )
    for key, version_id in list(ledger.versions):
        require(ledger.own_key(key), "cleanup_scope")
        parameters = {"Bucket": ledger.bucket, "Key": key}
        if version_id is not None:
            parameters["VersionId"] = version_id
        try:
            response = client.delete_object(**parameters)
            # If versioning changed during the test, do not leave a new marker.
            if version_id is None and response.get("DeleteMarker") and response.get("VersionId"):
                marker = (key, response["VersionId"])
                ledger.versions.add(marker)
                client.delete_object(Bucket=ledger.bucket, Key=key, VersionId=marker[1])
                ledger.versions.discard(marker)
            ledger.versions.discard((key, version_id))
        except Exception:
            continue
    report["cleanup_pending_versions"] = len(ledger.versions)
    report["cleanup_uncertain_writes"] = len(ledger.uncertain_keys)
    report["checks"]["created_objects_cleaned"] = (
        "passed" if not ledger.versions and not ledger.uncertain_keys else "incomplete"
    )


def write_private(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")


def run(args) -> dict:
    ledger = Ledger(args.bucket, args.model_jobs if args.with_model else 0)
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "live_database_accessed": False,
        "hosted_cognito_tested": False,
        "local_database": "temporary_sqlite",
        "model_jobs_attempted": 0,
        "model_jobs_passed": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "usage_complete": True,
        "cleanup_pending_versions": 0,
        "cleanup_uncertain_writes": 0,
        "cleanup_manifest_written": False,
        "checks": {"live_strands_bedrock": "not_requested"},
    }
    try:
        with tempfile.TemporaryDirectory(prefix="otw-live-smoke-") as temporary:
            with isolated_runtime(args, Path(temporary), ledger) as (app, original_client):
                try:
                    with ExitStack() as stack:
                        clients = [
                            stack.enter_context(
                                TestClient(
                                    app, base_url=LOCAL_ORIGIN, client=("127.0.0.1", 45000 + i)
                                )
                            )
                            for i in range(3)
                        ]
                        first, second = (
                            fixture(clients[0], "merchandise"),
                            fixture(clients[1], "bakery"),
                        )
                        ledger.workspace_ids.update((first["wid"], second["wid"]))
                        report["checks"]["two_local_synthetic_workspaces"] = "passed"
                        check_budget(first, ledger, report)
                        check_storage(first, second, clients[2], report)
                        if args.with_model:
                            ledger.model_gate_open = True
                            check_models([first, second][: args.model_jobs], report)
                finally:
                    cleanup(ledger, args, original_client, report)
        report["status"] = (
            "passed" if report["checks"]["created_objects_cleaned"] == "passed" else "incomplete"
        )
    except CheckFailed as exc:
        report["status"], report["failed_check"] = "failed", str(exc)
    except Exception as exc:
        report["status"] = "failed"
        # Exception strings can contain a presigned URL or token. Emit only a
        # bounded AWS code/type, never provider messages, traces or responses.
        code = (
            exc.response.get("Error", {}).get("Code", "")
            if isinstance(exc, ClientError)
            else type(exc).__name__
        )
        report["error_type"] = (
            code if re.fullmatch(r"[A-Za-z0-9_]{1,80}", code) else "ProviderError"
        )
    report["cleanup_pending_versions"] = len(ledger.versions)
    report["cleanup_uncertain_writes"] = sum(ledger.uncertain_keys.values())
    if ledger.versions or ledger.uncertain_keys:
        report["checks"]["created_objects_cleaned"] = "incomplete"
        manifest = args.output.with_name(args.output.name + ".cleanup-" + uuid4().hex + ".json")
        write_private(
            manifest,
            {
                "bucket": args.bucket,
                "region": args.region,
                "versions": [
                    {"key": key, "version_id": version} for key, version in ledger.versions
                ],
                "uncertain_keys": sorted(ledger.uncertain_keys),
            },
        )
        report["cleanup_manifest_written"] = True
    report.update(ledger.counters)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="Authenticated local AWS profile")
    parser.add_argument("--bucket", required=True, help="Existing private application bucket")
    parser.add_argument("--region", required=True)
    parser.add_argument(
        "--output", required=True, type=Path, help="JSON report; no credentials or URLs"
    )
    parser.add_argument(
        "--cleanup-profile", help="Optional operator profile with DeleteObjectVersion"
    )
    parser.add_argument("--with-model", action="store_true", help="Opt into paid Nova inference")
    parser.add_argument("--model-id", choices=MODEL_IDS, default=MODEL_IDS[0])
    parser.add_argument("--model-jobs", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", args.bucket):
        parser.error("--bucket must be an existing S3 bucket name")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]", args.region):
        parser.error("--region must be an AWS region name")
    args.output = args.output.expanduser().resolve()
    require(not args.output.exists(), "report_already_exists_use_new_output")
    report = run(args)
    write_private(args.output, report)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
