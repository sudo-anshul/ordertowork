"""Provision the small Cognito, private S3 and cost-budget foundation for OrderToWork.

Requires an explicit AWS profile. Without --apply, only reads the caller identity
and writes a plan. Never creates users, sends messages or writes credentials.
"""

import argparse
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

MANAGED_BY = "ordertowork-aws-services"
LOCAL_ORIGIN = "http://localhost:5173"
BUDGET_START = datetime(2026, 9, 1, tzinfo=UTC)
BUDGET_END = datetime(2027, 9, 1, tzinfo=UTC)
SDK_CONFIG = Config(
    signature_version="v4",
    connect_timeout=5,
    read_timeout=30,
    retries={"mode": "standard", "total_max_attempts": 3},
)


def hosted_origin(value: str) -> str:
    parts = urlsplit(value)
    if not (
        parts.scheme == "https"
        and parts.hostname
        and not re.search(r"\s", parts.netloc)
        and not parts.username
        and not parts.password
        and parts.path in {"", "/"}
        and not parts.query
        and not parts.fragment
    ):
        raise argparse.ArgumentTypeError("Use an HTTPS origin without a path, query or credentials")
    return value.rstrip("/")


def owned(tags: dict, project: str, resource: str) -> None:
    if tags.get("Project") != project or tags.get("ManagedBy") != MANAGED_BY:
        raise RuntimeError(f"Refusing to update {resource}: project ownership tags do not match")


def error_code(error: ClientError) -> str:
    return error.response.get("Error", {}).get("Code", "")


def write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    # The manifest contains identifiers and settings, never tokens or credentials.
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as file:
        json.dump(manifest, file, indent=2, sort_keys=True)
        file.write("\n")
    temporary.replace(path)


def ensure_budget(client, account: str, project: str, tags: dict) -> dict:
    name = project + "-gross-cost"
    resource_arn = f"arn:aws:budgets::{account}:budget/{name}"
    spec = {
        "BudgetName": name,
        "BudgetLimit": {"Amount": "25", "Unit": "USD"},
        "BudgetType": "COST",
        "TimeUnit": "ANNUALLY",
        "TimePeriod": {"Start": BUDGET_START, "End": BUDGET_END},
        "CostTypes": {
            "IncludeCredit": False,
            "IncludeRefund": False,
            "IncludeTax": True,
            "IncludeSubscription": True,
            "IncludeUpfront": True,
            "IncludeRecurring": True,
            "IncludeOtherSubscription": True,
            "IncludeSupport": True,
            "IncludeDiscount": True,
            "UseBlended": False,
            "UseAmortized": False,
        },
    }
    try:
        client.describe_budget(AccountId=account, BudgetName=name)
    except ClientError as error:
        if error_code(error) != "NotFoundException":
            raise
        client.create_budget(
            AccountId=account,
            Budget=spec,
            ResourceTags=[{"Key": key, "Value": value} for key, value in tags.items()],
        )
    else:
        existing_tags = client.list_tags_for_resource(ResourceARN=resource_arn)["ResourceTags"]
        owned({tag["Key"]: tag["Value"] for tag in existing_tags}, project, name)
        client.update_budget(AccountId=account, NewBudget=spec)
    return {
        "name": name,
        "amount_usd": 25,
        "time_unit": "ANNUALLY",
        "start": BUDGET_START.isoformat(),
        "end": BUDGET_END.isoformat(),
        "scope": "account-wide cost before promotional credits and refunds",
        "subscriptions_created": False,
        "hard_spending_cap": False,
    }


def ensure_bucket(client, account: str, region: str, project: str, tags: dict) -> dict:
    name = f"{project}-{account}-{region}"
    params = {"Bucket": name, "ExpectedBucketOwner": account}
    try:
        client.head_bucket(**params)
    except ClientError as error:
        if error_code(error) not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        create = {"Bucket": name, "ObjectOwnership": "BucketOwnerEnforced"}
        if region != "us-east-1":
            create["CreateBucketConfiguration"] = {"LocationConstraint": region}
        client.create_bucket(**create)
        client.put_bucket_tagging(
            **params, Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in tags.items()]}
        )
    else:
        try:
            existing_tags = client.get_bucket_tagging(**params)["TagSet"]
        except ClientError as error:
            if error_code(error) != "NoSuchTagSet":
                raise
            existing_tags = []
        owned({tag["Key"]: tag["Value"] for tag in existing_tags}, project, name)
    client.put_public_access_block(
        **params,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    client.put_bucket_ownership_controls(
        **params, OwnershipControls={"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}
    )
    client.put_bucket_encryption(
        **params,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )
    client.put_bucket_versioning(**params, VersioningConfiguration={"Status": "Enabled"})
    client.put_bucket_lifecycle_configuration(
        **params,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": project + "-version-cleanup",
                    "Status": "Enabled",
                    "Filter": {"Prefix": ""},
                    "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
                    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                    "Expiration": {"ExpiredObjectDeleteMarker": True},
                }
            ]
        },
    )
    client.put_bucket_policy(
        **params,
        Policy=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "DenyInsecureTransport",
                        "Effect": "Deny",
                        "Principal": "*",
                        "Action": "s3:*",
                        "Resource": [f"arn:aws:s3:::{name}", f"arn:aws:s3:::{name}/*"],
                        "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                    }
                ],
            }
        ),
    )
    return {
        "bucket": name,
        "attachment_prefix": "attachments/",
        "agent_session_prefix": "agent-sessions/",
        "backup_prefix": "backups/",
        "encryption": "AES256",
        "versioning": True,
        "noncurrent_version_expiry_days": 30,
        "current_objects_expire": False,
    }


def pool_updates(client, existing: dict) -> dict:
    # Cognito resets omitted settings to defaults: preserve accepted fields.
    fields = client.meta.service_model.operation_model("UpdateUserPool").input_shape.members
    preserved = {key: value for key, value in existing.items() if key in fields}
    preserved.update(UserPoolId=existing["Id"], PoolName=existing["Name"])
    return preserved


def ensure_cognito(
    client, account: str, region: str, project: str, tags: dict, origins: list
) -> dict:
    pool_name, client_name = project + "-users", project + "-web"
    pools = [
        pool
        for page in client.get_paginator("list_user_pools").paginate(MaxResults=60)
        for pool in page["UserPools"]
        if pool["Name"] == pool_name
    ]
    if len(pools) > 1:
        raise RuntimeError(f"Multiple pools are named {pool_name}; refusing ambiguous updates")
    desired = {
        "UserPoolTier": "LITE",
        "DeletionProtection": "ACTIVE",
        "AutoVerifiedAttributes": ["email"],
        "UserAttributeUpdateSettings": {"AttributesRequireVerificationBeforeUpdate": ["email"]},
        "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
        "AccountRecoverySetting": {
            "RecoveryMechanisms": [{"Priority": 1, "Name": "verified_email"}]
        },
        "UserPoolTags": tags,
    }
    if pools:
        pool = client.describe_user_pool(UserPoolId=pools[0]["Id"])["UserPool"]
        owned(pool.get("UserPoolTags", {}), project, pool_name)
        if pool.get("UsernameAttributes") != ["email"]:
            raise RuntimeError("Existing pool is not configured for email sign-in")
        if pool.get("MfaConfiguration") == "ON" or pool.get("SmsConfiguration"):
            raise RuntimeError("Existing MFA/SMS settings need review; refusing to downgrade them")
        updates = pool_updates(client, pool)
        updates.update(desired)
        updates["UserPoolTags"] = {**pool.get("UserPoolTags", {}), **tags}
        client.update_user_pool(**updates)
    else:
        pool = client.create_user_pool(
            PoolName=pool_name,
            UsernameAttributes=["email"],
            UsernameConfiguration={"CaseSensitive": False},
            Schema=[{"Name": "email", "Required": True, "Mutable": True}],
            Policies={
                "PasswordPolicy": {
                    "MinimumLength": 12,
                    "RequireUppercase": True,
                    "RequireLowercase": True,
                    "RequireNumbers": True,
                    "RequireSymbols": True,
                    "TemporaryPasswordValidityDays": 3,
                }
            },
            MfaConfiguration="OFF",
            **desired,
        )["UserPool"]
    pool_id = pool["Id"]
    client.set_user_pool_mfa_config(
        UserPoolId=pool_id,
        MfaConfiguration="OPTIONAL",
        SoftwareTokenMfaConfiguration={"Enabled": True},
    )
    domain = f"{project}-{account}"
    described = client.describe_user_pool_domain(Domain=domain).get("DomainDescription", {})
    if described.get("UserPoolId"):
        if described["UserPoolId"] != pool_id:
            raise RuntimeError("Cognito domain belongs to a different pool; refusing changes")
        if described.get("ManagedLoginVersion", 1) != 1:
            client.update_user_pool_domain(Domain=domain, UserPoolId=pool_id, ManagedLoginVersion=1)
    else:
        client.create_user_pool_domain(Domain=domain, UserPoolId=pool_id, ManagedLoginVersion=1)
    callback_urls = {origin + "/api/auth/callback" for origin in origins}
    logout_urls = {origin + "/" for origin in origins}
    clients = [
        entry
        for page in client.get_paginator("list_user_pool_clients").paginate(
            UserPoolId=pool_id, MaxResults=60
        )
        for entry in page["UserPoolClients"]
        if entry["ClientName"] == client_name
    ]
    if len(clients) > 1:
        raise RuntimeError(f"Multiple clients are named {client_name}; refusing ambiguous updates")
    settings = {
        "UserPoolId": pool_id,
        "ClientName": client_name,
        "SupportedIdentityProviders": ["COGNITO"],
        "AllowedOAuthFlowsUserPoolClient": True,
        "AllowedOAuthFlows": ["code"],
        "AllowedOAuthScopes": ["openid", "email", "profile"],
        "ReadAttributes": ["email", "email_verified", "name"],
        # Cognito requires write access to required schema attributes, including
        # email. The pool keeps its old email until a replacement is verified.
        "WriteAttributes": ["email", "name"],
        "ExplicitAuthFlows": ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        "AccessTokenValidity": 10,
        "IdTokenValidity": 10,
        "RefreshTokenValidity": 1,
        "TokenValidityUnits": {
            "AccessToken": "minutes",
            "IdToken": "minutes",
            "RefreshToken": "days",
        },
        "EnableTokenRevocation": True,
        "PreventUserExistenceErrors": "ENABLED",
    }
    if clients:
        app_client = client.describe_user_pool_client(
            UserPoolId=pool_id, ClientId=clients[0]["ClientId"]
        )["UserPoolClient"]
        if app_client.get("ClientSecret"):
            raise RuntimeError("Existing client has a secret; refusing to replace its identity")
        callback_urls.update(app_client.get("CallbackURLs", []))
        logout_urls.update(app_client.get("LogoutURLs", []))
        fields = client.meta.service_model.operation_model(
            "UpdateUserPoolClient"
        ).input_shape.members
        update = {key: value for key, value in app_client.items() if key in fields}
        update.update(settings, CallbackURLs=sorted(callback_urls), LogoutURLs=sorted(logout_urls))
        app_client = client.update_user_pool_client(**update)["UserPoolClient"]
    else:
        app_client = client.create_user_pool_client(
            **settings,
            GenerateSecret=False,
            CallbackURLs=sorted(callback_urls),
            LogoutURLs=sorted(logout_urls),
        )["UserPoolClient"]
    return {
        "user_pool_id": pool_id,
        "client_id": app_client["ClientId"],
        "domain": f"https://{domain}.auth.{region}.amazoncognito.com",
        "tier": "LITE",
        "managed_login_version": 1,
        "admin_create_users_only": True,
        "mfa": "optional TOTP; no SMS",
        "callback_urls": sorted(callback_urls),
        "logout_urls": sorted(logout_urls),
        "users_created": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--project", default="ordertowork-hackathon")
    parser.add_argument("--hosted-origin", type=hosted_origin, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Create/update the named project resources"
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,28}[a-z0-9]", args.project):
        parser.error("Project must be 3–30 lowercase letters, digits or hyphens")
    if not re.fullmatch(r"(?:us|eu|ap|sa|ca|me|af|il|mx)-[a-z]+-[0-9]+", args.region):
        parser.error("Use a commercial AWS region, such as us-east-1")
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    identity = session.client("sts", config=SDK_CONFIG).get_caller_identity()
    account = identity["Account"]
    tags = {"Project": args.project, "ManagedBy": MANAGED_BY}
    origins = [LOCAL_ORIGIN, *args.hosted_origin]
    manifest = {
        "schema_version": 1,
        "updated_at": datetime.now(UTC).isoformat(),
        "status": "applying" if args.apply else "planned",
        "account_id": account,
        "region": args.region,
        "project": args.project,
        "profile": args.profile,
        "tags": tags,
        "origins_requested": origins,
        "resource_names": {
            "bucket": f"{args.project}-{account}-{args.region}",
            "user_pool": args.project + "-users",
            "client": args.project + "-web",
            "domain": f"{args.project}-{account}",
            "budget": args.project + "-gross-cost",
        },
        "notice": "A cost budget is a delayed accounting signal, not a spending stop or credit balance.",
    }
    write_manifest(args.output, manifest)
    if not args.apply:
        print(f"Plan written to {args.output}; no AWS resources changed.")
        return
    try:
        manifest["budget"] = ensure_budget(
            session.client("budgets", region_name="us-east-1", config=SDK_CONFIG),
            account,
            args.project,
            tags,
        )
        write_manifest(args.output, manifest)
        manifest["storage"] = ensure_bucket(
            session.client("s3", config=SDK_CONFIG), account, args.region, args.project, tags
        )
        write_manifest(args.output, manifest)
        manifest["cognito"] = ensure_cognito(
            session.client("cognito-idp", config=SDK_CONFIG),
            account,
            args.region,
            args.project,
            tags,
            origins,
        )
        manifest["app_env"] = {
            "OTW_AWS_REGION": args.region,
            "OTW_AUTH_MODE": "cognito",
            "OTW_COGNITO_REGION": args.region,
            "OTW_COGNITO_USER_POOL_ID": manifest["cognito"]["user_pool_id"],
            "OTW_COGNITO_CLIENT_ID": manifest["cognito"]["client_id"],
            "OTW_COGNITO_CLIENT_SECRET": "",
            "OTW_COGNITO_DOMAIN": manifest["cognito"]["domain"],
            "OTW_PLATFORM_ADMIN_SUBJECTS": "",
            "OTW_STORAGE_MODE": "s3",
            "OTW_S3_BUCKET": manifest["storage"]["bucket"],
        }
        manifest["status"] = "applied"
    except Exception as error:
        manifest["status"] = "incomplete"
        manifest["error_type"] = type(error).__name__
        write_manifest(args.output, manifest)
        raise
    write_manifest(args.output, manifest)
    print(f"AWS service configuration written to {args.output}; no users or messages created.")


if __name__ == "__main__":
    main()
