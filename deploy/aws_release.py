"""Push an existing ARM image and dispatch a budget-host release through SSM.

Without --apply this validates local manifests and prints a plan; it makes no AWS
calls. The archive contains only named deployment scripts and nonsecret settings.
It never rebuilds an image or copies the developer's environment/credentials.
"""

import argparse
import base64
import gzip
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import boto3
from aws_services import SDK_CONFIG, owned, write_manifest
from botocore.config import Config
from botocore.exceptions import ClientError

DEPLOY = Path(__file__).resolve().parent
BUNDLE_FILES = (
    "budget-compose.yaml",
    "budget-Caddyfile",
    "budget-init-db.sh",
    "budget-config.py",
    "budget-backup.sh",
    "budget-setup.sh",
    "budget-install-aws.sh",
)


def budget_module():
    spec = importlib.util.spec_from_file_location("budget_config", DEPLOY / "budget-config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_manifests(host: dict, services: dict) -> None:
    for key in ("account_id", "project", "region"):
        if not host.get(key) or host[key] != services.get(key):
            raise ValueError(f"Host/services manifest mismatch: {key}")
    if services.get("status") != "applied":
        raise ValueError("AWS services must have an applied manifest before a release")
    if not re.fullmatch(r"[0-9]{12}", host["account_id"]):
        raise ValueError("Invalid AWS account identifier")
    if host["region"] != "us-east-1":
        raise ValueError("This budget release currently supports us-east-1 only")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,28}[a-z0-9]", host["project"]):
        raise ValueError("Invalid project name")
    if not re.fullmatch(r"i-[a-f0-9]{17}", host.get("instance_id", "")):
        raise ValueError("A valid prepared EC2 instance is required")
    expected_repository = (
        f"{host['account_id']}.dkr.ecr.{host['region']}.amazonaws.com/{host['project']}"
    )
    if host.get("repository_uri") != expected_repository:
        raise ValueError("ECR repository does not match the deployment account/region/project")
    if services.get("app_env", {}).get("OTW_COGNITO_CLIENT_SECRET"):
        raise ValueError(
            "Secret-bearing Cognito configuration cannot be included in a release archive"
        )
    origin = "https://" + host["domain"]
    cognito = services.get("cognito", {})
    if origin + "/api/auth/callback" not in cognito.get("callback_urls", []):
        raise ValueError("Register this host's HTTPS Cognito callback before releasing")
    if origin + "/" not in cognito.get("logout_urls", []):
        raise ValueError("Register this host's HTTPS Cognito logout URL before releasing")


def release_config(
    host: dict, services: dict, digest: str, *, enable_demo: bool = False
) -> dict[str, str]:
    module = budget_module()
    allowed = module.REQUIRED | module.OPTIONAL
    settings = {
        key: value
        for key, value in services["app_env"].items()
        if key in allowed and key != "OTW_COGNITO_CLIENT_SECRET"
    }
    settings.update(
        OTW_IMAGE=host["repository_uri"] + "@" + digest,
        OTW_DOMAIN=host["domain"],
        OTW_AWS_REGION=host["region"],
        OTW_DEMO_ENABLED="true" if enable_demo else "false",
    )
    settings.setdefault("OTW_BEDROCK_ENDPOINT", "runtime")
    settings.setdefault(
        "OTW_BEDROCK_MODEL_ID",
        "qwen.qwen3-235b-a22b-2507"
        if settings["OTW_BEDROCK_ENDPOINT"] == "mantle"
        else "us.amazon.nova-lite-v1:0",
    )
    if settings.get("OTW_S3_BUCKET") != services["resource_names"]["bucket"]:
        raise ValueError("Runtime bucket differs from the prepared services bucket")
    with tempfile.TemporaryDirectory(prefix="otw-release-config-") as temporary:
        path = Path(temporary) / "config.json"
        path.write_text(json.dumps(settings))
        return module.load_config(path)


def build_archive(settings: dict[str, str]) -> bytes:
    output = io.BytesIO()
    members = []
    for name in BUNDLE_FILES:
        path = DEPLOY / name
        if not path.is_file() or path.is_symlink() or path.resolve().parent != DEPLOY:
            raise ValueError(f"Deployment bundle requires a regular local file: {name}")
        members.append(("deploy/" + name, path.read_bytes()))
    members.append(
        ("config.json", (json.dumps(settings, indent=2, sort_keys=True) + "\n").encode())
    )
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, content in members:
                info = tarfile.TarInfo(name)
                info.mode, info.uid, info.gid, info.mtime = 0o600, 0, 0, 0
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def docker(*arguments: str, environment: dict | None = None, password: str | None = None) -> str:
    result = subprocess.run(
        ["docker", *arguments],
        env=environment,
        input=password,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        # Docker/credential helpers can include authentication material in errors.
        raise RuntimeError(
            f"Docker {arguments[0]} failed (exit {result.returncode}); output suppressed"
        )
    return result.stdout


def push_if_missing(ecr, host: dict, tag: str, local_image: str) -> str:
    repository = ecr.describe_repositories(repositoryNames=[host["project"]])["repositories"][0]
    if repository["repositoryUri"] != host["repository_uri"]:
        raise ValueError("Discovered ECR repository differs from the host manifest")
    if repository["imageTagMutability"] != "IMMUTABLE":
        raise ValueError("The release repository must use immutable tags")
    tags = ecr.list_tags_for_resource(resourceArn=repository["repositoryArn"])["tags"]
    owned({item["Key"]: item["Value"] for item in tags}, host["project"], host["project"])

    def digest_for_tag():
        try:
            details = ecr.describe_images(
                repositoryName=host["project"], imageIds=[{"imageTag": tag}]
            )["imageDetails"]
        except ecr.exceptions.ImageNotFoundException:
            return None
        digest = details[0]["imageDigest"]
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            raise ValueError("ECR returned an invalid image digest")
        return digest

    existing = digest_for_tag()
    if existing:
        return existing
    architecture = json.loads(
        docker("image", "inspect", "--format", "{{json .Architecture}}", "--", local_image)
    )
    if architecture != "arm64":
        raise ValueError("The supplied local image must have been built/tested for linux/arm64")
    # A temporary DOCKER_CONFIG would otherwise lose the active Desktop socket.
    original_endpoint = json.loads(
        docker("context", "inspect", "--format", "{{json .Endpoints.docker.Host}}")
    )
    if os.environ.get("DOCKER_HOST") and not os.environ.get("DOCKER_CONTEXT"):
        original_endpoint = os.environ["DOCKER_HOST"]
    if not isinstance(original_endpoint, str) or not original_endpoint.startswith("unix://"):
        raise ValueError(
            "This release helper requires an explicitly observed local Unix Docker socket"
        )
    registry = host["repository_uri"].split("/", 1)[0]
    authorization = ecr.get_authorization_token(registryIds=[host["account_id"]])[
        "authorizationData"
    ][0]
    if authorization["proxyEndpoint"] != "https://" + registry:
        raise ValueError("ECR authentication endpoint differs from the expected registry")
    username, password = (
        base64.b64decode(authorization["authorizationToken"], validate=True).decode().split(":", 1)
    )
    if username != "AWS":
        raise ValueError("Unexpected ECR authentication username")
    tagged_image = host["repository_uri"] + ":" + tag
    with tempfile.TemporaryDirectory(prefix="otw-docker-auth-") as temporary:
        environment = os.environ.copy()
        environment.pop("DOCKER_CONTEXT", None)
        environment.update(DOCKER_CONFIG=temporary, DOCKER_HOST=original_endpoint)
        docker(
            "login",
            "--username",
            "AWS",
            "--password-stdin",
            registry,
            environment=environment,
            password=password,
        )
        docker("tag", local_image, tagged_image, environment=environment)
        docker("push", tagged_image, environment=environment)
    digest = digest_for_tag()
    if not digest:
        raise RuntimeError(
            "The pushed image is not yet visible in ECR; retry this immutable release"
        )
    return digest


def verify_host(session, host: dict) -> None:
    identity = session.client("sts", config=SDK_CONFIG).get_caller_identity()
    if identity["Account"] != host["account_id"]:
        raise ValueError("Authenticated AWS profile belongs to a different account")
    reservations = session.client("ec2", config=SDK_CONFIG).describe_instances(
        InstanceIds=[host["instance_id"]]
    )["Reservations"]
    instances = [instance for entry in reservations for instance in entry["Instances"]]
    if len(instances) != 1 or any(entry["OwnerId"] != host["account_id"] for entry in reservations):
        raise ValueError("Prepared instance ownership could not be established")
    instance = instances[0]
    owned(
        {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])},
        host["project"],
        host["instance_id"],
    )
    if instance["State"]["Name"] != "running" or instance["Architecture"] != "arm64":
        raise ValueError("The prepared host must be running on ARM64")
    if instance.get("PublicIpAddress") != host.get("public_ip"):
        raise ValueError("Host public address changed; verify DNS and update the manifest first")


def upload_archive(s3, host: dict, bucket: str, tag: str, contents: bytes) -> tuple[str, str]:
    key = f"releases/{tag}/archive.tar.gz"
    digest = hashlib.sha256(contents).hexdigest()
    common = {"Bucket": bucket, "Key": key, "ExpectedBucketOwner": host["account_id"]}
    try:
        previous = s3.head_object(**common)
    except ClientError as error:
        if error.response["Error"]["Code"] not in {"404", "NoSuchKey", "NotFound"}:
            raise
    else:
        if previous.get("Metadata", {}).get("archive-sha256") != digest:
            raise ValueError(
                "This release tag already has a different bundle; use a fresh commit tag"
            )
        return key, digest
    s3.put_object(
        **common,
        Body=contents,
        ContentType="application/gzip",
        ServerSideEncryption="AES256",
        ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode(),
        Metadata={"archive-sha256": digest, "release-tag": tag},
    )
    return key, digest


def ssm_commands(region: str, bucket: str, key: str, tag: str, digest: str) -> list[str]:
    quote = shlex.quote
    directory = "/opt/ordertowork-release/" + tag
    archive = directory + "/archive.tar.gz"
    checksum = directory + "/archive.sha256"
    return [
        "set -eu",
        "umask 077",
        "exec 9>/var/lock/ordertowork-release.lock",
        "flock -n 9 || { printf 'Another deployment is running.\\n' >&2; exit 1; }",
        "unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE AWS_DEFAULT_PROFILE",
        "export DEBIAN_FRONTEND=noninteractive AWS_EC2_METADATA_DISABLED=false",
        "bash -c " + quote((DEPLOY / "budget-install-aws.sh").read_text()),
        f"install -d -m 0700 {quote(directory)}",
        f"aws s3 cp {quote('s3://' + bucket + '/' + key)} {quote(archive)} "
        f"--region {quote(region)} --only-show-errors",
        f"printf '%s  %s\\n' {quote(digest)} {quote(archive)} > {quote(checksum)}",
        f"sha256sum --check {quote(checksum)}",
        f"tar --extract --gzip --file {quote(archive)} --directory {quote(directory)} "
        "--no-same-owner --no-same-permissions",
        f"bash {quote(directory + '/deploy/budget-setup.sh')} {quote(directory + '/config.json')}",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--services", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--tag", required=True, help="Immutable Git commit tag, 7–40 hexadecimal characters"
    )
    parser.add_argument(
        "--local-image", required=True, help="Existing tested linux/arm64 Docker image"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Push/upload and dispatch the host release"
    )
    parser.add_argument(
        "--enable-demo", action="store_true", help="Enable bounded, isolated guest demo sessions"
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[a-f0-9]{7,40}", args.tag):
        parser.error("--tag must be a lowercase Git commit hash (7–40 characters)")
    host, services = json.loads(args.host.read_text()), json.loads(args.services.read_text())
    validate_manifests(host, services)
    settings = release_config(host, services, "sha256:" + "0" * 64, enable_demo=args.enable_demo)
    preview_archive = build_archive(settings)
    plan = {
        "mode": "apply" if args.apply else "preview",
        "instance_id": host["instance_id"],
        "domain": host["domain"],
        "image_tag": host["repository_uri"] + ":" + args.tag,
        "local_image": args.local_image,
        "demo_enabled": args.enable_demo,
        "archive_members": ["deploy/" + name for name in BUNDLE_FILES] + ["config.json"],
        "approximate_archive_bytes": len(preview_archive),
    }
    if not args.apply:
        print(json.dumps(plan, indent=2))
        return
    session = boto3.Session(profile_name=args.profile, region_name=host["region"])
    verify_host(session, host)
    digest = push_if_missing(
        session.client("ecr", config=SDK_CONFIG), host, args.tag, args.local_image
    )
    settings = release_config(host, services, digest, enable_demo=args.enable_demo)
    bucket = services["resource_names"]["bucket"]
    key, archive_digest = upload_archive(
        session.client("s3", config=SDK_CONFIG), host, bucket, args.tag, build_archive(settings)
    )
    # Retrying an uncertain send could start another command; let the operator inspect SSM.
    ssm = session.client(
        "ssm", config=Config(connect_timeout=5, read_timeout=30, retries={"total_max_attempts": 1})
    )
    response = ssm.send_command(
        InstanceIds=[host["instance_id"]],
        DocumentName="AWS-RunShellScript",
        Comment="OrderToWork release " + args.tag,
        TimeoutSeconds=600,
        Parameters={
            "commands": ssm_commands(host["region"], bucket, key, args.tag, archive_digest),
            "executionTimeout": ["1800"],
        },
    )
    host.update(
        image_digest=digest,
        image_uri=host["repository_uri"] + "@" + digest,
        release_tag=args.tag,
        release_key=key,
        release_archive_sha256=archive_digest,
        ssm_command_id=response["Command"]["CommandId"],
        release_dispatched_at=datetime.now(UTC).isoformat(),
        release_status="dispatched",
    )
    write_manifest(args.host, host)
    print(
        json.dumps(
            {
                "status": "dispatched; poll SSM before claiming deployment success",
                "ssm_command_id": host["ssm_command_id"],
                "instance_id": host["instance_id"],
                "image": host["image_uri"],
                "url": "https://" + host["domain"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
