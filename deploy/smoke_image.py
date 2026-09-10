"""Smoke-test the built image against local Compose PostgreSQL; no cloud calls or DB writes."""

import argparse
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4


def run(*args: str) -> str:
    result = subprocess.run(args, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def request(base: str, path: str, data: dict | None = None) -> tuple[int, dict, bytes]:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        base + path,
        data=body,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        response = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return (
            response.status,
            {key.lower(): value for key, value in response.headers.items()},
            response.read(),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="ordertowork:local")
    parser.add_argument("--network", default="ordertowork_default")
    parser.add_argument("--output", default="deploy/validation-smoke.json")
    args = parser.parse_args()
    database_url = os.environ.get(
        "OTW_SMOKE_DATABASE_URL",
        "postgresql+psycopg://ordertowork:ordertowork@db:5432/ordertowork",
    )
    if urlsplit(database_url).hostname not in {
        "db",
        "localhost",
        "127.0.0.1",
        "::1",
        "host.docker.internal",
    }:
        raise ValueError("Smoke verification accepts only a local test database host")
    environment = {
        "OTW_ENVIRONMENT": "production",
        "OTW_AUTH_MODE": "cognito",
        "OTW_APP_URL": "https://ordertowork.invalid",
        "OTW_STORAGE_MODE": "s3",
        "OTW_S3_BUCKET": "unconfigured-smoke-placeholder",
        "OTW_AGENT_MODE": "reference",
        "OTW_DATABASE_URL": database_url,
        "AWS_EC2_METADATA_DISABLED": "true",
    }
    flags = []
    for key, value in environment.items():
        flags.extend(["--env", f"{key}={value}"])
    image = json.loads(run("docker", "image", "inspect", args.image))[0]
    results = {
        "checked_at": datetime.now(UTC).isoformat(),
        "image_id": image["Id"],
        "platform": f"{image['Os']}/{image['Architecture']}",
        "aws_called": False,
        "database_mutations": False,
        "checks": {},
    }
    container_id = run(
        "docker",
        "run",
        "--detach",
        "--rm",
        "--name",
        "otw-smoke-" + uuid4().hex[:10],
        "--network",
        args.network,
        "--publish",
        "127.0.0.1::8000",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,noexec,size=64m",
        "--tmpfs",
        "/var/lib/ordertowork:rw,nosuid,noexec,uid=10001,gid=10001,size=64m",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "128",
        *flags,
        args.image,
    )
    try:
        address = run("docker", "port", container_id, "8000/tcp").splitlines()[0]
        base = "http://" + address
        deadline = time.monotonic() + 30
        while True:
            try:
                status, _, body = request(base, "/api/health")
                if status == 200:
                    assert json.loads(body)["status"] == "ok"
                    break
            except (urllib.error.URLError, ConnectionError):
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("Container did not become healthy within 30 seconds")
            time.sleep(0.25)
        results["checks"]["api_health"] = "passed"
        status, _, body = request(base, "/api/ready")
        assert status == 200 and json.loads(body)["status"] == "ready"
        results["checks"]["local_postgresql_readiness"] = "passed"
        status, headers, index = request(base, "/")
        assert status == 200 and b'<div id="root">' in index
        assert headers.get("x-content-type-options") == "nosniff"
        assert headers.get("referrer-policy") == "no-referrer"
        assert "content-security-policy" in headers
        status, _, deep = request(base, "/workspaces/smoke/orders")
        assert status == 200 and deep == index
        assets = re.findall(rb'(?:src|href)="(/assets/[^"?]+)"', index)
        assert assets
        for asset in assets:
            status, _, contents = request(base, asset.decode())
            assert status == 200 and len(contents) > 100
        assert request(base, "/api/not-a-real-route")[0] == 404
        assert request(base, "/assets/not-a-real-asset.js")[0] == 404
        results["checks"]["spa_fallback_assets_and_404s"] = "passed"
        assert request(base, "/api/auth/me")[0] == 401
        assert (
            request(
                base,
                "/api/auth/development-login",
                {
                    "email": "smoke@example.com",
                    "name": "Smoke",
                },
            )[0]
            == 404
        )
        results["checks"]["anonymous_and_development_auth_rejected"] = "passed"
        probe = run(
            "docker",
            "exec",
            container_id,
            "python",
            "-c",
            (
                "import os,json; from pathlib import Path; "
                "assert os.getuid()==10001; assert not Path('/app/.env').exists(); "
                "assert not Path('/app/frontend/node_modules').exists(); "
                "assert Path('/etc/ssl/certs/rds-global-bundle.pem').is_file(); "
                "print(json.dumps({'uid':os.getuid()}))"
            ),
        )
        assert json.loads(probe)["uid"] == 10001
        run(
            "docker",
            "exec",
            container_id,
            "uv",
            "run",
            "python",
            "-m",
            "ordertowork.worker",
            "--help",
        )
        revision = run("docker", "exec", container_id, "uv", "run", "alembic", "current")
        assert "(head)" in revision, revision
        results["checks"]["non_root_read_only_runtime_and_cli"] = "passed"
        results["checks"]["existing_database_migration_head"] = revision
        captured_logs = subprocess.run(
            ["docker", "logs", container_id], capture_output=True, text=True, check=True
        )
        logs = captured_logs.stdout + captured_logs.stderr
        assert '"GET /' not in logs and '"POST /' not in logs
        results["checks"]["access_logs_disabled"] = "passed"
    finally:
        subprocess.run(["docker", "rm", "--force", container_id], capture_output=True, check=False)
    # A fresh isolated process proves readiness fails when no database is reachable.
    bad_flags = []
    environment["OTW_DATABASE_URL"] = (
        "postgresql+psycopg://ordertowork:ordertowork@127.0.0.1:1/ordertowork?connect_timeout=1"
    )
    for key, value in environment.items():
        bad_flags.extend(["--env", f"{key}={value}"])
    run(
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        *bad_flags,
        "--entrypoint",
        "python",
        args.image,
        "-c",
        "from fastapi.testclient import TestClient; from ordertowork.main import app; "
        "client=TestClient(app); assert client.get('/api/health').status_code==200; "
        "assert client.get('/api/ready').status_code==503",
    )
    results["checks"]["readiness_without_database_is_503"] = "passed"
    Path(args.output).write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
