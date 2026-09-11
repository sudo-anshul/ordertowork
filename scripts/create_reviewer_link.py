"""Create a private reviewer link and hash-only deployment values outside the repository."""

import argparse
import hashlib
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit


def create_link(url: str, expires_at: str, output: Path) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Use the application's HTTPS origin without a path or credentials")
    expires = datetime.fromisoformat(expires_at)
    if expires.tzinfo is None or not datetime.now(UTC) < expires <= datetime.now(UTC) + timedelta(
        days=90
    ):
        raise ValueError("Use an aware expiry within the next 90 days")
    repository = Path(__file__).resolve().parents[1]
    output = output.resolve()
    if output.is_relative_to(repository):
        raise ValueError("Reviewer credentials must be stored outside the source repository")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    access = output / "reviewer-access.json"
    runtime = output / "reviewer-runtime.json"
    if access.exists() or runtime.exists():
        raise ValueError("Reviewer files already exist; refusing to overwrite a live access link")
    token = secrets.token_urlsafe(48)
    expiry = expires.astimezone(UTC).isoformat()
    for path, payload in (
        (
            access,
            {"url": url.rstrip("/") + "/review#" + token, "token": token, "expires_at": expiry},
        ),
        (
            runtime,
            {
                "OTW_REVIEWER_TOKEN_HASH": hashlib.sha256(token.encode()).hexdigest(),
                "OTW_REVIEWER_EXPIRES_AT": expiry,
            },
        ),
    ):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
    print(f"Private reviewer link saved to {access}; no token printed.")
    print(f"Hash-only deployment values saved to {runtime}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        create_link(args.url, args.expires_at, args.output)
    except (ValueError, OSError):
        parser.exit(
            1,
            "Could not create reviewer files. Check origin, expiry and a fresh directory outside the repository.\n",
        )


if __name__ == "__main__":
    main()
