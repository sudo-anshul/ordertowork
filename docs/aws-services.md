# Small AWS service foundation

`deploy/aws_services.py` configures the Cognito and S3 providers already used by the application and one account-wide cost budget. It does not create compute, databases, IAM identities, users, email subscriptions or Bedrock subscriptions. Run it with a reviewed setup profile; application processes should use their dedicated runtime role.

Preview the names and settings using an explicit profile:

```sh
uv run python deploy/aws_services.py --profile ordertowork --output ../../work/aws-services.json
```

The preview calls STS to identify the account and writes nonsecret identifiers. Apply the same plan by adding `--apply`. The default region is `us-east-1`, and the default project is `ordertowork-hackathon`. Names include the account identifier where global uniqueness is required. The script will reuse or update existing resources only when their exact names and `Project`/`ManagedBy` tags match. A same-name untagged resource causes an error rather than an implicit takeover. Run one setup process at a time.

Once the hosted application origin is known, rerun with its actual HTTPS origin:

```sh
uv run python deploy/aws_services.py --profile ordertowork --output ../../work/aws-services.json --apply --hosted-origin https://orders.example.com
```

That merges the hosted callback/logout URLs with existing entries. Local callback `http://localhost:5173/api/auth/callback` and logout `http://localhost:5173/` remain available. Omit an origin only when it has not yet been assigned; do not apply the illustrative hostname above. The manifest's `app_env` contains provider settings to combine with the chosen `OTW_APP_URL` and environment. It contains no AWS keys, Cognito secret, passwords or tokens. The script does not modify `.env` or copy a setup identity into the application.

The user pool uses the Cognito **Lite** tier, classic hosted UI, a public app client, authorization-code grant and the existing application's PKCE/state/nonce validation. Only an administrator can create accounts, so an unknown visitor cannot register and consume the shared model allowance. Provisioning itself creates no accounts and sends no messages. Create invited accounts separately with messages suppressed when conducting an authorized smoke test. Verify each actual user's email ownership before marking it verified. TOTP is optional and SMS is not configured; the application platform-administrator allowlist stays empty. Review and require appropriate MFA before adding support administrators. Pool deletion protection is active and must be explicitly disabled for eventual teardown.

S3 is private with all four Block Public Access controls, bucket-owner-enforced ownership, AES256/SSE-S3 default encryption, versioning, and a deny policy for non-TLS requests. Data is organized under `attachments/`, `agent-sessions/`, and `backups/`. Prefixes do not need placeholder objects. Noncurrent object versions expire after 30 days, unfinished multipart uploads after one day, and expired deletion markers are removed. Current objects are retained; this does not implement a database backup scheduler, account erasure or automatic current-backup deletion. Runtime IAM policies must grant each process only its required prefixes.

The **$25 annual budget covers 1 September 2026 through 1 September 2027**, across the account, with credits and refunds excluded so promotional credits cannot hide usage. It is an accounting warning point intended to preserve part of the $50 allowance for judging; it is **not a hard spending cap, real-time balance or shutdown action**. Annual budgets reset on AWS's annual accounting cadence; the end date bounds this budget's lifetime, and usage after that date is not covered. There are no notification subscriptions or budget actions. Inspect actual billing regularly and use the application's global job/request limits as additional controls. A new budget does not guarantee remaining credit, and incurred charges can appear with a delay.

The script writes progress after each completed service. An `incomplete` manifest means an error occurred after zero or more changes; investigate it and rerun against the same project. Existing completed resources are retained. A failure immediately after creating a resource but before ownership tagging can require the operator to inspect and finish tagging that known resource before retrying. AWS cloud behavior must still be verified with a fresh hosted login and a small authenticated S3 round-trip; offline SDK tests establish request compatibility only.
