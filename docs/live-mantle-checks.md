# Hosted Mantle verification

`scripts/check_live_mantle.py` checks the deployed judge workflow with real, paid Strands analysis. It is separate from the ordinary test suite and never runs automatically during CI or deployment.

## Review the plan without making requests

```bash
uv run python scripts/check_live_mantle.py --url https://YOUR_DEMO_HOST
```

Without `--run-paid-check`, the script only prints the plan. It creates no session and makes no network requests. The URL must be an HTTPS origin without a path, credentials, query or fragment; HTTP is allowed only on loopback for development.

## Run after deploying Mantle

```bash
uv run python scripts/check_live_mantle.py \
  --url https://YOUR_DEMO_HOST \
  --run-paid-check \
  --output ../../outputs/live-mantle-check.json
```

This opts into up to **two analysis submissions** in **one new guest session**: one merchandise order and one bakery order. A Strands analysis may contain multiple model calls, each charged by the provider; two submissions does not mean two inference calls. Existing demo/global allowance, turn and token limits remain active. No request or analysis retry is attempted. A failed or ambiguous HTTP request consumes the script's submission count and stops the run.

The script checks both workspaces and orders are synthetic, expiring demo data before submitting either seeded customer message. It polls each job for up to 120 seconds and requires:

- Successful `bedrock` execution with exactly one attempt, a complete usage record identifying `mantle` and `qwen.qwen3-235b-a22b-2507`, and positive token/model-call counts.
- Recorded order-context and preview tools, including the requested quantities of 45 merchandise items and 36 bakery items.
- Unchanged accepted revision, no missing details, and newly generated requested/feasible proposals. Prepared proposals cannot satisfy the check.
- A new feasible proposal shared for customer review, approval of its exact terms, a synthetic deposit record if needed, a matching production ticket, and production started on that revision.

Only synthetic sample-deposit records are created; the script does not call a payment provider or move money. Customer approval is exercised through a separate, unauthenticated HTTP client. The share link must point back to the configured origin. No email is sent. Guest logout is attempted in `finally`, including on failure.

## Interpret the report

A successful report has `status: passed`, two passing business checks, `analysis_submissions: 2`, and `guest_logout_succeeded: true`. Exit status is nonzero on any failed check. A missing-details result is an honest failure of this acceptance scenario, even when the worker job itself succeeded. The script does not manufacture proposals or switch to reference interpretation to make the check pass.

The optional JSON report contains only the server origin, timestamps, fixed check labels, profile names, numeric usage, and outcome counts. It excludes cookies, CSRF tokens, guest identities, order/message IDs, source text, customer links, raw API bodies and raw server errors. Do not enable HTTP debug logging around this tool. The report is operational evidence, not an AWS bill or a broad model-quality benchmark.

On a timeout or failure, inspect the sanitized report before deciding on any further run. The job may still finish on the server after a client timeout. Re-running creates another guest session and may spend more of the shared judging allowance; it is never automatic. Logout ends access but does not promise immediate deletion of sample records or model session traces.
