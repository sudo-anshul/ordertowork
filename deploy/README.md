# Deployment runbook

The current $50 hackathon deployment uses the [single-host runbook](budget-README.md), [AWS service setup](../docs/aws-services.md) and `aws_budget_host.py`. That setup avoids an idle load balancer, NAT gateway and managed database. This document describes the more expensive managed ECS/RDS topology as a future option. Provider and deployment validation must be recorded separately; a runbook or valid JSON policy is not evidence of a working cloud deployment.

## Container and CI

The root Dockerfile builds the frontend with Node 22, installs the locked production Python dependencies with uv 0.11.7 and Python 3.12, then runs the application as UID 10001. It contains the built frontend and migrations. The image starts the API; run the worker as a separate service using the same image. Source `.env` files, credentials, local databases, tests and caches are excluded from the build context.

```sh
docker build --tag ordertowork:local .
```

With the local Compose database already running and migrated, run the packaged runtime check:

```sh
python deploy/smoke_image.py
```

This starts a temporary container on the observed `ordertowork_default` network, checks the bundled frontend/API and PostgreSQL readiness, verifies non-root/read-only operation and CLI availability, then removes its containers. It reads the existing migration version without changing business data. Override `--network` for a differently named Compose project; `OTW_SMOKE_DATABASE_URL` can provide a different **local test** database connection without printing it. The script also checks readiness failure in a network-isolated process. Its Cognito/S3 configuration is deliberately incomplete and no AWS calls occur. Docker bridge peers are not loopback peers, so this production smoke does not enable local development login; use the repository's direct localhost development servers for that flow.

The GitHub Actions workflow runs backend/migration/script lint, migrations, Alembic schema-drift checks and tests with a disposable PostgreSQL 17 service on port 55432, then builds the frontend and container. It does not publish an image or access AWS. CI uses `reference` agent mode and explicitly local development authentication; this is test configuration. The workflow must run successfully in the actual repository before claiming remote CI passed.

Run database migrations once per release, with an authorized migration connection:

```sh
uv run alembic upgrade head
```

For the worker service, override the image command with:

```sh
uv run python -m ordertowork.worker
```

For an ECS migration task, override the command with `uv run alembic upgrade head`. Inspect its exit code before rolling out API or worker tasks. Do not run migrations concurrently in every API replica. Use a separate database role with DDL privileges for migrations; the running application should have only schema usage and the table/sequence privileges its operations require. Verify the migration role's default privileges also grant access to newly created tables.

## AWS topology and creation order

Use one chosen AWS region where the intended Bedrock model, ECS/Fargate and other required services are available. Confirm the exact model and inference-profile access in this account first. Do not silently use the Strands SDK default model or assume credits have been approved.

1. Create an ECR repository, enable image scanning and publish the tested image for the selected CPU architecture. Pin the release by image digest. A build on an ARM Mac does not establish that an x86 Fargate image works; explicitly build `linux/amd64` for x86 tasks or use ARM64 Fargate with a tested ARM64 image.
2. Create a VPC with private application and database subnets. Keep RDS private. Allow PostgreSQL port 5432 only from the API, worker and migration task security groups. Require HTTPS at the public ingress and restrict API port 8000 to the load balancer security group. Do not expose the worker or database publicly.
3. Create an encrypted RDS PostgreSQL 17 database and store its credentials in Secrets Manager. Enable deletion protection, automatic backups and a retention window suited to the business; seven days is a reasonable initial policy to review. Single-AZ is a prototype availability tradeoff, not a high-availability service. Test restoration separately.
4. Create a private S3 bucket with Block Public Access, bucket-owner-enforced object ownership, default SSE-S3 encryption, versioning and a policy denying non-TLS access. The current file writer explicitly requests AES256/SSE-S3; switching to a bucket that requires SSE-KMS also requires adapting that writer and granting only the relevant KMS key actions. This is not an implemented KMS configuration.
5. Configure the Cognito user pool and app client described below. Set the final HTTPS application URL and callback/logout allowlists together.
6. Create separate API and worker task roles, an ECS task execution role and the named CloudWatch log groups. Store database/client secrets as ECS secret references, never in the image or committed files.
7. Create the API service using **ECS Express Mode** or a conventional ECS Fargate service behind an HTTPS Application Load Balancer. Express Mode provisions the Fargate service, HTTPS URL, load balancer and supporting infrastructure. Use the chosen VPC/network configuration so the API can reach private RDS. Configure the target health check to `/api/ready`, which checks database connectivity; `/api/health` checks only application liveness.
8. Create a conventional ECS worker service from the same image, with the worker command above and no load balancer. Start with one API task and one worker task. Job leases and database transactions remain required when scaling. Do not rely on web traffic to keep a background process alive.
9. Run the migration task, then start/roll out API and worker services. Complete the release checks below before accepting real business data.

ECS Express Mode is a verified current AWS option. **AWS App Runner no longer accepts new customers from 30 April 2026** and is not the setup path for this new deployment. AgentCore is optional future work; the implemented worker and PostgreSQL queue do not require it.

Private tasks need explicit outbound connectivity for Cognito's token endpoint/JWKS, Bedrock, S3, ECR, Secrets Manager and logs. Choose tested VPC endpoints and/or NAT routing. An S3 gateway endpoint can reduce S3 NAT traffic, but does not solve access to every other endpoint. NAT, load balancer, RDS and Fargate can incur charges while idle; inspect estimated recurring costs before creating them.

## Cognito, sessions and administrator access

Create a user pool with email sign-in and required verified email. Enable the authorization-code grant and `openid email profile` scopes for an app client, and configure a Cognito managed-login domain. The backend implements code + S256 PKCE, browser-bound state, nonce, JWKS signature verification, issuer/audience checks and one-time callback consumption. A confidential client secret is supported and belongs in Secrets Manager; a client without a secret still uses PKCE.

Allow exactly these application URLs, substituting the actual HTTPS origin:

- Callback: `https://orders.example.com/api/auth/callback`
- Sign-out: `https://orders.example.com/`

The API exchanges the code and issues an opaque HttpOnly session cookie. Only the session hash is persisted. The app does not expose Cognito tokens to frontend JavaScript or store refresh tokens. Sessions have an absolute configured expiry; users sign in again after expiry. Cookie-authenticated mutations require the session's CSRF token. Logout revokes the local session and returns Cognito's logout URL; the frontend should visit it to clear the managed-login session too.

**Require MFA for administrative accounts at the identity provider.** The simplest current configuration is a Cognito pool that requires MFA for every account, using an appropriate supported factor. The application does not independently enforce a per-request MFA claim, so an optional-MFA pool plus an undocumented convention is insufficient for administrators.

`OTW_PLATFORM_ADMIN_SUBJECTS` is a comma-separated allowlist of exact verified Cognito `sub` values. Leave it empty until a reviewed support administrator is needed. A workspace owner cannot grant platform access through the UI. A stored user flag alone does not authorize platform access. Keep support permissions limited to the current metadata view; there is no general customer impersonation feature.

Development login works only with `OTW_ENVIRONMENT=development`, `OTW_AUTH_MODE=development`, a loopback application URL, a loopback request host and a loopback direct peer. It is refused in production. Explicit local support testing may allowlist `development:normalized-email@example.com`; these are unverified local test identities and must never be copied into the production allowlist.

## Production configuration

Supply these values through the ECS task definition and secret references. Placeholder values below are not usable credentials.

| Variable | Production value/purpose |
|---|---|
| `OTW_ENVIRONMENT` | `production` |
| `OTW_APP_URL` | Final HTTPS origin, such as `https://orders.example.com` |
| `OTW_AUTH_MODE` | `cognito` |
| `OTW_SESSION_COOKIE` | `__Host-otw_session` for HTTPS, host-only, root-path cookies |
| `OTW_SESSION_HOURS` | `12` initially; review business risk and session policy |
| `OTW_COGNITO_REGION` | Actual user-pool region |
| `OTW_COGNITO_USER_POOL_ID` | Actual pool identifier |
| `OTW_COGNITO_CLIENT_ID` | Actual app-client identifier |
| `OTW_COGNITO_CLIENT_SECRET` | Secret reference if the app client has a secret |
| `OTW_COGNITO_DOMAIN` | HTTPS managed-login/custom domain, without a path |
| `OTW_PLATFORM_ADMIN_SUBJECTS` | Empty by default; exact reviewed Cognito subjects only |
| `OTW_DATABASE_URL` | Secret containing the PostgreSQL URL, with TLS verification |
| `OTW_STORAGE_MODE` | `s3` |
| `OTW_S3_BUCKET` | Private application bucket name |
| `OTW_AWS_REGION` | Actual S3/Bedrock region used by the app |
| `OTW_AGENT_MODE` | `bedrock` for the real agent; `reference` is explicitly non-AI test mode |
| `OTW_BEDROCK_ENDPOINT` | `mantle` for the selected Qwen model; defaults to `runtime` for existing installations |
| `OTW_BEDROCK_MANTLE_PROJECT_ID` | `default` initially; must match the authorized Mantle project |
| `OTW_BEDROCK_MODEL_ID` | Explicit accessible model/inference-profile ID |
| `OTW_AGENT_TIMEOUT_SECONDS` | `120` initially |
| `OTW_WORKER_LEASE_SECONDS` | `180` initially, longer than the inference timeout plus commit allowance |
| `OTW_MAX_JOB_ATTEMPTS` | `3` initially |
| `OTW_MAX_DAILY_AGENT_JOBS` | `100` initially per workspace per UTC day; this is not a global cloud-spend cap |
| `OTW_MAX_UPLOAD_BYTES` | `5242880` initially |

A database URL can use `?sslmode=verify-full&sslrootcert=/etc/ssl/certs/rds-global-bundle.pem`. URL-encode special characters in the username/password. The image includes the public AWS RDS CA bundle downloaded from the official trust store; review and refresh it for certificate rotation. Never disable TLS verification to fix a connection error.

The current service enforces tenant access in application queries and membership checks. PostgreSQL row-level security is not claimed as implemented. Do not share the application's database credentials with customers or rely on a tenant ID supplied by a model. Team addition requires an existing verified account; the explicit localhost mode also permits its clearly marked development identities.

## IAM examples

The JSON files in this directory are **examples requiring substitution and account-specific validation**, not a deployable stack. They deliberately do not grant provisioning powers to running application tasks.

- `iam-api-task.example.json`: object access under `attachments/` only. Delete supports upload cleanup; application routes must still authorize any deletion.
- `iam-worker-task.example.json`: session objects/listing under `agent-sessions/`, plus the selected Bedrock resources. Remove the unused model/profile entry. Cross-region inference profiles require permissions for the profile and every actual destination foundation-model ARN; determine those targets explicitly before testing.
- `iam-task-trust.example.json`: ECS task trust restricted to the deployment account and region. Use it for the relevant ECS roles after replacement.
- `iam-execution-role.example.json`: ECR pull, named log groups and named deployment secrets. ECR authorization requires `Resource: "*"`; repository pulls are separately restricted. Pre-create the log groups. Add a narrowly scoped `kms:Decrypt` permission only if the referenced secrets use a customer-managed KMS key.

The task execution role injects secrets and pulls images. The application task role supplies SDK permissions. Cognito's code exchange/JWKS validation uses public HTTPS endpoints and does not require granting the app Cognito administration actions. Model subscription/first-use setup belongs to the operator's setup identity, not the runtime role. Validate substituted policies with IAM Access Analyzer and an actual least-privilege smoke test before rollout.

## Release, recovery and privacy checks

Verify a fresh Cognito login, verified-email handling, sign-out and expired session; confirm remote development login is rejected. Test two business tenants and owner/operator restrictions. Upload and retrieve an authorized attachment, and deny a foreign tenant. Exercise both order profiles, customer approval, deposit gating and the production ticket. Run one real Strands tool/structured-output invocation, then restart the worker during a job and verify recovery without duplicate commitments. Confirm stale proposals and insufficient capacity preserve the previously accepted order. A green HTTP health check cannot establish these behaviors.

Observe failed/retried jobs, queue age, worker liveness, database connection limits, token usage and AWS charges. Configure job limits and spending alerts before raising traffic. AWS budgets and alerts do not provide a hard spending cap. Preserve generic error messages and structured identifiers; do not log credentials, raw attachments, customer approval tokens or full request bodies. The image disables Uvicorn access logs because customer capability tokens occur in URLs. Keep any load-balancer/proxy access-log policy compatible with that restriction.

Workspace archival preserves records; it is not erasure. Treat export, account deletion, retention expiry and removal from S3 object versions/backups as separate operational obligations. Before onboarding real customers, define which data must be retained and for how long, add/run the required export/deletion procedures, and test them. Do not promise automated erasure from immutable backups or completed third-party exports. Preserve business audit events without unnecessary personal content.

Test a database restore into a separate isolated environment and verify the corresponding attachment/version references. Take a pre-migration backup for significant schema changes; prefer compatible schema changes and an application-image rollback. A previous image may not work against a destructive new schema, and restoring an old database can discard later orders. Record the actual recovery point and recovery time achieved by the exercise.

## AWS references

- [ECS Express Mode overview](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-overview.html)
- [ECS Express Mode creation](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-create-full.html)
- [App Runner availability change](https://docs.aws.amazon.com/apprunner/latest/dg/apprunner-availability-change.html)
- [Bedrock model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)
- [Cognito authorization endpoint](https://docs.aws.amazon.com/cognito/latest/developerguide/authorization-endpoint.html)
- [Cognito token endpoint](https://docs.aws.amazon.com/cognito/latest/developerguide/token-endpoint.html)
- [Cognito ID token](https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-the-id-token.html)
- [Cognito MFA configuration](https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-mfa.html)
- [S3 presigned URL behavior](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html)
- [RDS public CA bundle](https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem)

No CloudFormation template is included because cloud resource creation and integration could not be validated in the unauthenticated account.
