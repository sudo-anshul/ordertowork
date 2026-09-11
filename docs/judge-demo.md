# One-click judge demo

The landing page offers **Try the live demo** alongside the normal business sign-in. Demo entry creates an anonymous application session and two isolated, synthetic workspaces: merchandise and bakery. It opens a sample order directly, with no Cognito account, password, email or setup required. Cognito remains mandatory for business accounts in production.

The prepared order contains an accepted baseline, a sample customer change and saved example proposals. These are labeled synthetic; opening the example makes no model call. A judge can inspect constraints, share a revised proposal, open the customer approval page, record a sample deposit and produce a production ticket. New analysis uses the configured agent provider and reports provider failures honestly. The saved example remains usable if new inference is unavailable or the demo allowance has been reached.

## Try a live agent run

Choose **Add customer message** on a sample order and enter a fictional request. Saving starts a fresh analysis; opening the prepared proposals does not. After completion, expand **Request check complete** to inspect the model and provider recorded by the server, the Strands tool checks, token usage, source evidence and any missing details. Failed runs retain an execution record when available and do not count as a completed analysis.

The selected live provider is **Qwen3 235B A22B 2507** (`qwen.qwen3-235b-a22b-2507`) through **AWS Bedrock Mantle**, using the Strands OpenAI-compatible model adapter. The execution record identifies the actual model used per run rather than inferring it from deployment configuration. The model interprets text; deterministic server tools check prices, resources and order constraints. File attachments do not undergo image interpretation. The owner reviews proposals and the customer approves an exact revision before production can proceed.

## Isolation and lifetime

Every new visitor receives a random, unverified guest identity with two private workspaces. Repeating the entry request with a valid guest session resumes that session. It never replaces a valid business session. Expiry is enforced by the server for sessions, workspace operations, customer links and queued agent work; the browser also returns an expired guest to the entry page.

Guests can exercise the seeded order workflow and view inventory, but cannot create business workspaces, manage members, change workspace settings, upload files or access platform administration. Guest workspace expiry is separate from `is_demo`: examples created by signed-in business users do not acquire guest restrictions. Expiry ends access; it is not a promise of immediate database or backup deletion. Only synthetic information should be entered in guest workspaces.

## Default limits

| Setting | Default | Effect |
| --- | --- | --- |
| `OTW_DEMO_ENABLED` | `false` | Opt-in guest access; disabling also blocks existing guest workspaces. |
| `OTW_DEMO_SESSION_MINUTES` | `60` | Guest session and workspace lifetime. |
| `OTW_MAX_DAILY_DEMO_SESSIONS` | `50` | Shared admission limit across visitors, reset at 00:00 UTC. |
| `OTW_MAX_DEMO_AGENT_JOBS` | `2` | Analysis allowance per guest workspace, including paid retries. |
| `OTW_MAX_DAILY_DEMO_BEDROCK_ATTEMPTS` | `20` | Shared daily paid demo allowance, also subject to the overall Bedrock limit. |

These are transactional application limits, not an AWS dollar spending cap. A failed inference may still be billed. The business global limit, output-token and agent-turn limits continue to apply. Refreshing, creating another workspace or restarting the worker does not reset shared usage. A daily count limit can also be reached by automated traffic; use the feature switch and global budget switch to pause access when necessary.

## Enable on the budget host

Use the existing release command with `--enable-demo`. The image must contain the demo migration and frontend changes. Releasing without this flag disables guest access by default; business Cognito login continues to work.

```bash
uv run python deploy/aws_release.py \
  --host ../../work/aws-host.json \
  --services ../../work/aws-services.json \
  --profile ordertowork \
  --tag COMMIT_HASH \
  --local-image ordertowork:COMMIT_HASH \
  --enable-demo --apply
```

The release script validates the nonsecret configuration, deploys the immutable image through SSM, and runs migrations before starting the API and worker. Inspect SSM completion and verify `/api/auth/config` reports `demo_enabled: true` before advertising the judge link.

## Verification

Backend checks cover guest isolation, expiry, protected business actions and budget limits. PostgreSQL tests use unique temporary schemas to exercise concurrent admission and budget reservation. Guest fixtures also run against dates beyond the original event week, including a daylight-saving transition.

After deployment, verify the complete guest flow in a fresh browser, ensure another browser cannot read its workspace, confirm logout returns to the application rather than Cognito, and verify the business sign-in link still opens Cognito. A successful demo-entry or health check does not establish that Bedrock inference is available.

## Protected reviewer access

A private reviewer link is available separately from the public demo. It opens two isolated synthetic workspaces without signup, keeps access until an explicitly configured UTC deadline, and offers **Start fresh examples** for repeating the workflow. Reviewers can use owner features such as creating orders/workspaces, changing business rules and uploading sample files. Platform administration remains restricted to authorized operators.

Reviewer sessions do not consume public admission, per-workspace demo, daily workspace or daily public AI allowances. Paid reviewer attempts are metered separately in `agent_daily_usage.reviewer_attempts`; queued reviewer work takes priority over queued public work. A currently running model call is not interrupted. Per-run turn/token/time bounds and the owner's explicit global inference stop switch (`OTW_MAX_DAILY_BEDROCK_ATTEMPTS=0`) still apply. This is not a dollar-denominated AWS spending cap: keep the private link out of public pages and reserve actual cloud funds for its use.

Generate a cryptographically random link outside the repository:

```bash
uv run python scripts/create_reviewer_link.py \
  --url https://orders.example.com \
  --expires-at 2026-10-16T00:00:00Z \
  --output /path/outside/repository/private-reviewer
```

The tool writes mode-0600 `reviewer-access.json` (private URL/token) and `reviewer-runtime.json` (hash/expiry only), refusing to overwrite an existing link. Transfer **only** the latter file's `OTW_REVIEWER_TOKEN_HASH` and `OTW_REVIEWER_EXPIRES_AT` values into the deployment service manifest's `app_env`. The existing release helper validates and carries them into API/worker configuration. Deploy the migration and matching frontend/backend together. No new AWS service is required.

Share the private URL only through the intended reviewer instructions. The token is carried in the URL fragment, exchanged through a POST and removed from the address bar; it is never stored in localStorage/sessionStorage or embedded in public source. A server-side hash binds sessions and workspaces to the configured access. Expiry, clearing both configuration values, or rotating the hash disables old sessions, customer links and queued/result-application paths. Expiry does not erase stored data or backups.

If a normal business session is already open, the page asks the visitor to switch explicitly before replacing it. Otherwise the link opens the workbench automatically. Reloading resumes the private session. Opening **Start fresh examples** creates new isolated sample workspaces and revokes the previous browser session; ordinary business records are untouched.

Verify a release with the existing two-analysis check, adding `--reviewer-access-file /private/path/reviewer-access.json` to `scripts/check_live_mantle.py`. The file's origin must match `--url`; tokens must not be passed as CLI arguments, committed, or included in screenshots/reports. The check still requires `--run-paid-check`, does not retry inference automatically, and emits sanitized evidence only. Verify public-quota exhaustion, revocation and isolation locally before using paid inference.
