#!/usr/bin/env python3
"""Validate host deployment values and create environment files without shell eval."""

import argparse
import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

REQUIRED = {
    "OTW_IMAGE",
    "OTW_DOMAIN",
    "OTW_AWS_REGION",
    "OTW_S3_BUCKET",
    "OTW_COGNITO_REGION",
    "OTW_COGNITO_USER_POOL_ID",
    "OTW_COGNITO_CLIENT_ID",
    "OTW_COGNITO_DOMAIN",
    "OTW_BEDROCK_MODEL_ID",
}
OPTIONAL = {
    "OTW_COGNITO_CLIENT_SECRET",
    "OTW_PLATFORM_ADMIN_SUBJECTS",
    "OTW_SESSION_HOURS",
    "OTW_MAX_DAILY_AGENT_JOBS",
    "OTW_MAX_JOB_ATTEMPTS",
    "OTW_AGENT_TIMEOUT_SECONDS",
    "OTW_WORKER_LEASE_SECONDS",
    "OTW_MAX_DAILY_BEDROCK_ATTEMPTS",
    "OTW_BEDROCK_MAX_OUTPUT_TOKENS",
    "OTW_AGENT_MAX_TURNS",
    "OTW_AGENT_MAX_TOTAL_TOKENS",
    "OTW_DEMO_ENABLED",
    "OTW_DEMO_SESSION_MINUTES",
    "OTW_MAX_DAILY_DEMO_SESSIONS",
    "OTW_MAX_DEMO_AGENT_JOBS",
    "OTW_MAX_DAILY_DEMO_BEDROCK_ATTEMPTS",
    "OTW_POSTGRES_IMAGE",
    "OTW_CADDY_IMAGE",
}
FIXED = {
    "OTW_ENVIRONMENT": "production",
    "OTW_AUTH_MODE": "cognito",
    "OTW_STORAGE_MODE": "s3",
    "OTW_AGENT_MODE": "bedrock",
    "OTW_SESSION_COOKIE": "__Host-otw_session",
    "OTW_MAX_UPLOAD_BYTES": "5242880",
    "AWS_EC2_METADATA_DISABLED": "false",
}
DEFAULTS = {
    "OTW_SESSION_HOURS": "12",
    "OTW_PLATFORM_ADMIN_SUBJECTS": "",
    "OTW_MAX_DAILY_AGENT_JOBS": "20",
    "OTW_MAX_JOB_ATTEMPTS": "1",
    "OTW_AGENT_TIMEOUT_SECONDS": "120",
    "OTW_WORKER_LEASE_SECONDS": "180",
    "OTW_MAX_DAILY_BEDROCK_ATTEMPTS": "100",
    "OTW_BEDROCK_MAX_OUTPUT_TOKENS": "1024",
    "OTW_AGENT_MAX_TURNS": "5",
    "OTW_AGENT_MAX_TOTAL_TOKENS": "18000",
    "OTW_DEMO_ENABLED": "false",
    "OTW_DEMO_SESSION_MINUTES": "60",
    "OTW_MAX_DAILY_DEMO_SESSIONS": "50",
    "OTW_MAX_DEMO_AGENT_JOBS": "2",
    "OTW_MAX_DAILY_DEMO_BEDROCK_ATTEMPTS": "20",
}
IMAGE = re.compile(
    r"[0-9]{12}\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com(?:\.cn)?/"
    r"[a-z0-9._/-]+@sha256:[a-f0-9]{64}"
)
DOMAIN = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?"
)


def load_config(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("Deployment configuration must be a JSON object")
    missing = REQUIRED - payload.keys()
    unknown = payload.keys() - REQUIRED - OPTIONAL
    if missing:
        raise ValueError("Missing deployment fields: " + ", ".join(sorted(missing)))
    if unknown:
        raise ValueError("Unsupported deployment fields: " + ", ".join(sorted(unknown)))
    values = {key: str(value) for key, value in payload.items()}
    for key, value in values.items():
        if any(char in value for char in "\r\n\x00'$\\"):
            raise ValueError(f"Unsupported character in {key}")
        if "REPLACE" in value or (key in REQUIRED and not value):
            raise ValueError(f"A real deployment value is required for {key}")
    image = IMAGE.fullmatch(values["OTW_IMAGE"])
    if not image or image.group(1) != values["OTW_AWS_REGION"]:
        raise ValueError("OTW_IMAGE must be an immutable ECR digest in OTW_AWS_REGION")
    if not DOMAIN.fullmatch(values["OTW_DOMAIN"]):
        raise ValueError("OTW_DOMAIN must be a DNS hostname, without a scheme or path")
    for key in ("OTW_AWS_REGION", "OTW_COGNITO_REGION"):
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]", values[key]):
            raise ValueError(f"Invalid region format in {key}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", values["OTW_S3_BUCKET"]):
        raise ValueError("Invalid S3 bucket name")
    domain = urlsplit(values["OTW_COGNITO_DOMAIN"])
    if (
        domain.scheme != "https"
        or not domain.hostname
        or domain.username
        or domain.password
        or domain.port
        or domain.path not in ("", "/")
        or domain.query
        or domain.fragment
    ):
        raise ValueError("OTW_COGNITO_DOMAIN must be an HTTPS origin")
    values["OTW_COGNITO_DOMAIN"] = values["OTW_COGNITO_DOMAIN"].rstrip("/")
    resolved = DEFAULTS | values
    for key in (
        "OTW_SESSION_HOURS",
        "OTW_MAX_DAILY_AGENT_JOBS",
        "OTW_MAX_JOB_ATTEMPTS",
        "OTW_AGENT_TIMEOUT_SECONDS",
        "OTW_WORKER_LEASE_SECONDS",
        "OTW_BEDROCK_MAX_OUTPUT_TOKENS",
        "OTW_AGENT_MAX_TURNS",
        "OTW_AGENT_MAX_TOTAL_TOKENS",
    ):
        if not resolved[key].isdigit() or int(resolved[key]) < 1:
            raise ValueError(f"{key} must be a positive integer")
    if not resolved["OTW_MAX_DAILY_BEDROCK_ATTEMPTS"].isdigit():
        raise ValueError("OTW_MAX_DAILY_BEDROCK_ATTEMPTS must be a nonnegative integer")
    if resolved["OTW_DEMO_ENABLED"] not in {"true", "false"}:
        raise ValueError("OTW_DEMO_ENABLED must be true or false")
    for key, lower, upper in (
        ("OTW_DEMO_SESSION_MINUTES", 5, 120),
        ("OTW_MAX_DAILY_DEMO_SESSIONS", 0, 200),
        ("OTW_MAX_DEMO_AGENT_JOBS", 0, 5),
        ("OTW_MAX_DAILY_DEMO_BEDROCK_ATTEMPTS", 0, 100),
    ):
        if not resolved[key].isdigit() or not lower <= int(resolved[key]) <= upper:
            raise ValueError(f"{key} must be an integer between {lower} and {upper}")
    if int(resolved["OTW_WORKER_LEASE_SECONDS"]) <= int(resolved["OTW_AGENT_TIMEOUT_SECONDS"]) + 15:
        raise ValueError("The worker lease must exceed the inference timeout by over 15 seconds")
    return resolved


def read_generated_env(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)='([^']*)'", line)
        if not match:
            raise ValueError(f"Unrecognized generated environment format in {path.name}")
        result[match.group(1)] = match.group(2)
    return result


def write_env(path: Path, values: dict[str, str]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write("# Generated on the deployment host; never commit or publish.\n")
            for key, value in sorted(values.items()):
                stream.write(f"{key}='{value}'\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_config(config: dict[str, str], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    compose_path = directory / "compose.env"
    previous = read_generated_env(compose_path) if compose_path.exists() else {}
    if previous:
        for key in ("OTW_POSTGRES_PASSWORD", "OTW_APP_DATABASE_PASSWORD"):
            if not re.fullmatch(r"[a-f0-9]{64}", previous.get(key, "")):
                raise ValueError("Existing database credentials are incomplete; refusing rotation")
    admin_password = previous.get("OTW_POSTGRES_PASSWORD") or secrets.token_hex(32)
    app_password = previous.get("OTW_APP_DATABASE_PASSWORD") or secrets.token_hex(32)
    compose = {
        "OTW_IMAGE": config["OTW_IMAGE"],
        "OTW_DOMAIN": config["OTW_DOMAIN"],
        "OTW_POSTGRES_PASSWORD": admin_password,
        "OTW_APP_DATABASE_PASSWORD": app_password,
        "OTW_MIGRATION_DATABASE_URL": f"postgresql+psycopg://otw_migrator:{admin_password}@db:5432/ordertowork",
    }
    for key in ("OTW_POSTGRES_IMAGE", "OTW_CADDY_IMAGE"):
        if key in config:
            compose[key] = config[key]
    runtime = {
        key: value
        for key, value in config.items()
        if key not in {"OTW_IMAGE", "OTW_DOMAIN", "OTW_POSTGRES_IMAGE", "OTW_CADDY_IMAGE"}
    }
    runtime |= FIXED | {
        "OTW_APP_URL": "https://" + config["OTW_DOMAIN"],
        "OTW_DATABASE_URL": f"postgresql+psycopg://ordertowork:{app_password}@db:5432/ordertowork",
        "AWS_DEFAULT_REGION": config["OTW_AWS_REGION"],
    }
    write_env(compose_path, compose)
    write_env(directory / "runtime.env", runtime)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("validate", "write", "registry", "backup-settings"))
    parser.add_argument("configuration", type=Path)
    parser.add_argument("directory", type=Path, nargs="?")
    args = parser.parse_args()
    try:
        if args.operation == "backup-settings":
            config = read_generated_env(args.configuration)
            print(config["OTW_AWS_REGION"], config["OTW_S3_BUCKET"])
            return
        config = load_config(args.configuration)
        if args.operation == "write":
            if not args.directory:
                raise ValueError("write requires the deployment directory")
            write_config(config, args.directory)
        elif args.operation == "registry":
            print(config["OTW_AWS_REGION"], config["OTW_IMAGE"].split("/", 1)[0])
        else:
            print("Deployment configuration validated; no credentials printed.")
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f"Configuration error: {error}\n")


if __name__ == "__main__":
    main()
