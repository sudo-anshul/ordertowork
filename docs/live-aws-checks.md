# Live AWS verification

`deploy/live_smoke.py` exercises the application's real private S3 upload and download path while every user, workspace, order, job and session lives in a new temporary SQLite database. It does not connect to the hosted database or read repository `.env` settings. It creates two explicitly synthetic workspaces with different localhost test owners. Those identities are unverified development fixtures, not Cognito accounts.

Run the storage check from the repository after authenticating a local AWS profile and creating the private application bucket:

```sh
uv run python deploy/live_smoke.py \
  --profile ordertowork \
  --region us-east-1 \
  --bucket YOUR_EXISTING_PRIVATE_BUCKET \
  --output ../../work/live-s3-check.json
```

Choose a new output filename for each run. The script refuses to overwrite evidence. It uses the chosen profile for SDK requests and prints a JSON report containing check statuses and counts. S3 smoke requests get one transport attempt so a repeated versioned upload cannot silently leave an unreported version; optional model calls and exact cleanup get at most two attempts. It does not print session cookies, credentials, object keys, presigned URLs, provider error bodies or customer content. The resulting JSON file has mode `0600`.

The storage check uploads one tiny synthetic PDF through the real application API. It verifies the stored digest, the application's HTTPS presign redirect, downloaded bytes and attachment disposition. It removes the signature for a separate anonymous S3 request and expects denial. Separate local owner sessions verify that the other workspace cannot obtain the attachment, including by substituting its ID into an otherwise authorized order path. A job queued with the daily live-AI limit set to zero must fail before any provider call. No Bedrock inference is requested by this command.

## Optional bounded model check

After Bedrock account verification/model access is ready, add `--with-model` and choose a new output file:

```sh
uv run python deploy/live_smoke.py \
  --profile ordertowork \
  --region us-east-1 \
  --bucket YOUR_EXISTING_PRIVATE_BUCKET \
  --output ../../work/live-model-check.json \
  --with-model
```

One merchandise job is attempted by default. `--model-jobs 2` adds the bakery case; two is the maximum, and there is no automatic job retry. The script only accepts `us.amazon.nova-lite-v1:0` or `amazon.nova-lite-v1:0` through `--model-id`. It fixes each run at five model turns, 1,024 output tokens per response, an 18,000-token cumulative soft limit, and a 120-second worker timeout. The invocation token limit is checked between turns and can be exceeded by the final response; it is not an AWS dollar limit. Transport attempts can repeat a failed request. The JSON reports observed token counts, which can be incomplete after a failed stream and are not a billing statement.

Both cases use the existing synthetic fixtures' explicit September 2026 dates, quantities and specifications. Successful checks require real Strands database-read and preview events, a completed structured change interpretation, a proposal with the requested quantity, and preservation of the accepted revision and recorded deposit. S3 also receives the isolated Strands session snapshot. A failed model check is reported as failed; the script does not substitute reference interpretation or retry until the account is ready. Repeated runs have separate temporary budgets, so operators must still control how often they run it against the $50 allowance.

## Exact cleanup

The harness records every successful S3 write's exact key and returned `VersionId`. Normal cleanup deletes only those recorded versions, including a delete marker if application rollback created one. It never lists or sweeps the bucket and never touches hosted application objects. Fresh workspace UUIDs isolate every run under the application's existing `attachments/` and `agent-sessions/` prefixes.

The smoke profile needs the application's read/write permissions. Removing specific versions requires `s3:DeleteObjectVersion`, which a production runtime role may intentionally lack. Supply an existing operator profile for cleanup when needed:

```sh
uv run python deploy/live_smoke.py \
  --profile YOUR_RUNTIME_TEST_PROFILE \
  --cleanup-profile YOUR_OPERATOR_PROFILE \
  --region us-east-1 \
  --bucket YOUR_EXISTING_PRIVATE_BUCKET \
  --output ../../work/live-s3-role-check.json
```

Cleanup failures produce an incomplete/failed result and a private sidecar named `<output>.cleanup-<random-id>.json`. That sidecar contains only the bucket, region and exact object/version identifiers needing attention. Keep it local: the normal report intentionally excludes those identifiers. The script does not broaden IAM access on its own. An operator can remove a recorded version with `s3api delete-object` and its exact `--version-id`. Do not replace that with a bucket-wide deletion or a versionless delete, which can leave a marker and retained versions. An uncertain write means the SDK did not return a successful upload response; inspect that exact key before deciding which version to remove. The harness leaves uncertain keys for operator review instead of guessing.

## Hosted Cognito and deployment checks

The script intentionally does not claim a hosted-login or PostgreSQL deployment test. Complete those on the actual HTTPS application using a fresh browser session:

1. Sign in through the configured Cognito hosted domain with an invited test account. Follow the callback to the application and confirm that the signed-in identity is the intended account. No tokens should be exposed in frontend storage or copied into a report.
2. Confirm that the remote development-login route is refused. Create only a clearly labeled synthetic workspace for the deployment check.
3. Run the normal application flow with a synthetic order. Check the owner review, customer approval, deposit requirement and production ticket. Model access can remain unavailable while these non-AI steps are checked; label any reference interpretation explicitly.
4. Sign out, return to a protected page and confirm that sign-in is required. Use another invited account to check tenant separation through the deployed application.
5. Record which steps actually passed and which remain pending. Healthy API/readiness endpoints, offline SDK contracts, this SQLite provider smoke, and a hosted browser flow prove different parts of the system.

Bedrock account verification can temporarily block an otherwise correct integration. Record the provider's actual result and retry only after access is ready; do not use a larger model or repeatedly invoke inference to probe the same pending account state.
