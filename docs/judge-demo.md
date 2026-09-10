# One-click judge demo

The landing page offers **Try the live demo** alongside the normal business sign-in. Demo entry creates an anonymous application session and two isolated, synthetic workspaces: merchandise and bakery. It opens a sample order directly, with no Cognito account, password, email or setup required. Cognito remains mandatory for business accounts in production.

The prepared order contains an accepted baseline, a sample customer change and saved example proposals. These are labeled synthetic; opening the example makes no model call. A judge can inspect constraints, share a revised proposal, open the customer approval page, record a sample deposit and produce a production ticket. New analysis uses the configured agent provider and reports provider failures honestly. The saved example remains usable while Bedrock account verification is pending.

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
