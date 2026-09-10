# Budget deployment for the hackathon

Run the API, worker, PostgreSQL 17, and Caddy on one ARM64 EC2 `t4g.small`. Keep private attachments and agent sessions in S3, authentication in Cognito, and model inference in Bedrock. This removes the idle charges of a load balancer, NAT gateway, and RDS instance. It preserves the database transactions and separate worker used by the application.

This is a single-host deployment. An instance failure causes downtime until it is recovered, and a host loss can require restoring the latest database backup. It is suitable for this small hackathon budget; it is not a claim of high availability. The API and worker share the EC2 instance role. Container isolation does not give them independent IAM identities.

## Spending envelope

Planning estimates for Linux in `us-east-1`, before taxes and any account-specific pricing:

| Item | Assumption | 29 days continuously running |
|---|---|---:|
| EC2 `t4g.small` | $0.0168/hour, 696 hours | $11.69 |
| One public IPv4 address | $0.005/hour, 696 hours | $3.48 |
| Encrypted gp3 root disk | 12 GiB, approximately $0.08/GiB-month | $0.92 |
| Base infrastructure | Compute, one IPv4 address, disk | **About $16.10** |

S3 storage/requests, ECR storage, outbound transfer, Cognito features, Bedrock tokens, and any billing services are additional. These numbers are estimates, not a free-tier claim or a hard cap. Verify the region's current prices and the account's actual bill before provisioning. Keep at least **$25 of the $50** available for judging and unexpected usage; restrict development inference to a few dollars. The remaining margin covers the small storage and request bill.

Use **standard CPU credits**, not unlimited, so a busy burst cannot generate surplus CPU-credit charges. Performance can be throttled when credits are depleted. Do not build application images on the 2 GiB host: build and test ARM64 locally, push to ECR, and deploy the immutable digest. Retain only the releases needed for rollback.

The deployment defaults are 20 requested jobs per workspace/day, 100 model attempts globally/day, one job attempt, 1,024 output tokens per model call, five agent turns, and an 18,000-token agent limit. Failed invocations can still cost money and consume an attempt. A count/token limit is not a dollar-denominated AWS cap. Lower the global attempt limit during development. `OTW_MAX_DAILY_BEDROCK_ATTEMPTS=0` is the application inference stop switch after restarting API and worker with the updated environment.

AWS Budgets and billing alerts are delayed notifications; they do not halt charges. Monitor actual use and leave capacity for the judges. Pausing the worker stops new model work while keeping manual order management available. Stopping EC2 stops compute charges but **EBS storage and a retained public IPv4 address continue billing**.

## Provisioning contract

The deployment operator creates the AWS resources. These host scripts do not create a second cloud stack.

- A public subnet with an Internet Gateway and route, one `t4g.small` instance using Ubuntu 24.04 ARM64, and a stable Elastic IP. No NAT gateway, load balancer, or publicly exposed database.
- An encrypted gp3 root disk of at least 12 GiB. Leave enough space for the OS, two application releases, database, bounded container logs, a 1 GiB swap file, and backups. Check disk usage before increasing retention.
- EC2 metadata tokens required (**IMDSv2**) and response hop limit **2** so the API and worker containers can obtain temporary role credentials. No access keys, CLI browser-login files, or developer `.env` files on the host.
- Inbound TCP 80 and 443 only. No public 22, 5432, or 8000. Use Systems Manager Session Manager/Run Command for administration; verify the SSM agent is running on the chosen image.
- An instance profile with SSM connectivity; ECR authorization plus pull actions on the single release repository; S3 attachment/session-prefix permissions; `s3:PutObject` on `backups/*`; and invocation permission for the selected Bedrock profile and its actual destination model ARNs. Do not attach administrator access to the runtime instance.
- One S3 bucket with Block Public Access, bucket-owner-enforced ownership, AES256 default encryption, versioning, and deny-non-TLS policy. Keep the `backups/` prefix private. Set a reviewed lifecycle policy for backup objects and noncurrent versions; the host only prunes local dumps.
- A Cognito user pool and app client supporting authorization code + PKCE and verified email. Prefer a client without a secret for this deployment. Configure the exact HTTPS callback `https://DOMAIN/api/auth/callback` and logout `https://DOMAIN/`. Keep the platform administrator allowlist empty unless needed; require appropriate MFA before enabling it.

The Canonical public SSM parameter for the intended architecture is `/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id`; verify its resolved AMI in the chosen region before launch. Configure standard CPU credits explicitly when launching the T4g instance.

Bedrock account authorization is independent of IAM permission. The selected low-cost model is `us.amazon.nova-lite-v1:0`; verify its availability in the account. When AWS denies model access, this deployment stays in honest `bedrock` mode and records a failed analysis. It must not be presented as successful live AI or silently replaced with reference output.

## HTTPS

Set `OTW_DOMAIN` to a hostname resolving to the Elastic IP. An owned domain is preferable. If none is available, a verified public-IP alias such as `203.0.113.10.sslip.io` can be used for this demo by replacing the example IP with the actual reserved address. This depends on a third-party DNS service and a certificate authority accepting that hostname. Verify DNS resolution and ACME issuance before putting it in the submission.

Caddy obtains and renews the certificate and redirects HTTP to HTTPS. Keep its named data/config volumes so renewals reuse their state. TCP 80 and 443 must reach the host for certificate challenges. Changing the hostname requires matching Cognito callback/logout updates. Do not disable TLS verification or use an insecure cookie to bypass a DNS/certificate problem.

Caddy caps request bodies at 6 MB, with a 30-second body-read timeout. The application attachment limit remains 5 MiB. Only Caddy publishes ports; the database is on an internal Docker network. Uvicorn trusts forwarding headers from this controlled proxy network. Access logging is disabled, and Caddy filters request URI/header fields in structured logs because customer capability tokens can occur in URLs.

## First release

1. Build/test the release locally for `linux/arm64`, push it to the ECR repository, and record the digest. The application image is required in digest form. PostgreSQL and Caddy defaults are also pinned to the locally verified image digests; update deliberately when applying upstream fixes.
2. Upload only the `deploy/budget-*` release files to a private deployment prefix or transfer them using an authorized administrative channel. Extract them on the instance into a staging directory. No source credential files are required.
3. Copy `budget-config.example.json` to a root-owned configuration file on the host and replace the placeholders. A Cognito client secret, if used, belongs only in that root-readable host configuration or a dedicated secret delivery mechanism. Do not paste secrets in SSM command text or logs.
4. Run, for example:

   ```sh
   sudo bash /root/release/deploy/budget-setup.sh /root/ordertowork-config.json
   ```

The script validates settings, installs Docker/Compose and the AWS CLI on Ubuntu, generates credentials on the host, authenticates ECR using the instance role, and pulls images. It starts PostgreSQL, runs migrations once, and then starts the API, worker, and proxy. It also enables a twice-daily S3 database-backup timer. It uses a temporary Docker configuration for the ECR token and deletes it when finished.

The generated `/opt/ordertowork/compose.env` and `/opt/ordertowork/runtime.env` have mode 0600 and are never shell-sourced. Database passwords are generated once and preserved across releases. The application has a separate PostgreSQL login with table DML/sequence access; the migration role owns the schema. The API and worker environment does not contain the database administrator password. Within this single host, PostgreSQL uses its private Docker network; an eventual remote database must use verified TLS.

The PostgreSQL named volume holds business data independently of container replacement. The bootstrap refuses to generate new passwords if an existing data volume has lost its credential file. Back up those host credentials through an appropriate secure operator process, or use the database-admin recovery procedure; do not keep the only recovery material inside a disposable container.

Memory ceilings are PostgreSQL 384 MiB, API 384 MiB, worker 640 MiB, and Caddy 96 MiB. This leaves roughly 500 MiB for the host and Docker; swap absorbs brief spikes. Container logs rotate at 5 MB × 3 files per service. Swap is not additional throughput, and memory limits do not establish load-tested capacity.

## Verification and updates

An HTTP health check is necessary but does not prove cloud integration. Before sharing the URL, verify:

1. Public HTTPS and `/api/ready`; a real Cognito login/logout; refusal of remote development login.
2. A new workspace, order, customer decision, deposit requirement, and production ticket, including owner/operator separation.
3. A small attachment upload/download with private S3 access and a denied foreign-workspace request.
4. One bounded Strands/Bedrock run, its actual tool execution, and the resulting proposal. If AWS account authorization is still blocked, record that unresolved blocker.
5. A database dump uploaded to the private `backups/` prefix, followed by a restore into a separate disposable database and a data check.

To inspect service state without printing credentials:

```sh
sudo docker compose --env-file /opt/ordertowork/compose.env \
  -f /opt/ordertowork/deploy/budget-compose.yaml ps
sudo systemctl status ordertowork-backup.timer
sudo journalctl -u ordertowork-backup.service --since today
```

For an update, stage the new `budget-*` files and configuration with the new application digest, then rerun the setup command. The script preserves passwords, drains the worker/API, backs up an existing schema to S3, runs migrations, and restarts services. This intentionally causes a short maintenance window. If the backup or migration fails, stop and resolve it before restarting against an uncertain schema. Keep the prior image and a verified pre-migration dump; application rollback alone is insufficient after an incompatible schema migration.

Never run `docker compose down --volumes` during an ordinary update. This deletes the database and certificate volumes. Do not use broad Docker volume-prune commands on the deployment host.

## Backup and recovery

`budget-backup.sh` produces a PostgreSQL custom-format dump under `/var/backups/ordertowork`, uploads it with SSE-S3 to `s3://BUCKET/backups/`, and prunes only its own successful local backups after seven days. A file lock prevents overlapping timers. If the upload fails, the completed local dump remains and the service reports failure. Review backup-service errors; a timer being enabled is not evidence that backups work.

The timer runs twice daily, so the expected database recovery point can be up to 12 hours old, plus any missed backups. Actual recovery time must be measured. Dumps do not contain S3 attachment bytes or Cognito identities. Preserve the matching bucket/pool and review their retention separately.

For a restore drill, download a dump using an authorized identity, create a fresh isolated PostgreSQL database, and run `pg_restore --exit-on-error --no-owner` as its migration owner. Confirm schema version, representative order history, reservations, and attachment references. To replace a live database, first stop API/worker and preserve a current dump. Restore into a new volume, validate it, then switch the application deliberately. Do not overwrite the only working volume as a test.

## Stop and cleanup

To stop new model work immediately while leaving the site usable:

```sh
sudo docker compose --env-file /opt/ordertowork/compose.env \
  -f /opt/ordertowork/deploy/budget-compose.yaml stop worker
```

Set the global attempt limit to zero and recreate both API and worker for a persistent application-level inference stop. Temporarily deny the runtime role's Bedrock invocation permission if an independent cloud-side stop is required; already completed calls may still be billed.

After judging, verify the latest backup, disable the backup timer, stop containers, and decide whether any data must be exported or retained. To end recurring infrastructure charges, terminate the instance, delete any retained EBS volumes/snapshots that are no longer needed, and **release the Elastic IP**. Remove unused ECR images, S3 objects and noncurrent versions, Cognito resources, runtime roles/policies, and any paid logging/monitoring resources created for the deployment. A versioned S3 bucket requires deliberate version/delete-marker cleanup. These are destructive cleanup actions; make a reviewed retention decision first.

Record the retained resources and their cost rather than assuming that stopping EC2 makes the deployment free.
