# Bedrock Mantle deployment

OrderToWork selects `qwen.qwen3-235b-a22b-2507` in `us-east-1`, using Strands' OpenAI-compatible provider against AWS Bedrock Mantle. Model catalog visibility and a successful operator request do not establish runtime-role access: verify the deployed worker with its instance role before claiming the integration is live.

Set these nonsecret values in the services manifest's `app_env` before generating the release:

```json
{
  "OTW_BEDROCK_ENDPOINT": "mantle",
  "OTW_BEDROCK_MODEL_ID": "qwen.qwen3-235b-a22b-2507",
  "OTW_BEDROCK_MANTLE_PROJECT_ID": "default"
}
```

The release helper preserves explicit model choices. An omitted endpoint remains `runtime` for backward compatibility; only an explicitly selected Mantle endpoint defaults to Qwen. The budget config still forces `OTW_AGENT_MODE=bedrock` so paid-attempt limits cover both endpoints. Keep the global, workspace, guest, turn, token and timeout limits active. A zero global Bedrock-attempt limit remains the application kill switch.

## Runtime permissions

Generate a policy locally; this command makes no AWS calls:

```bash
python deploy/mantle_policy.py --account 570082421930 --region us-east-1 \
  --model qwen.qwen3-235b-a22b-2507 --project default \
  --output /tmp/ordertowork-mantle-policy.json
```

`bedrock-mantle:CreateInference` is scoped to the account's `project/default` ARN and the exact `bedrock-mantle:Model` condition. `bedrock-mantle:CallWithBearerToken` requires `Resource: "*"` because AWS defines no resource type for that action; it is restricted to `SHORT_TERM` tokens and `us-east-1`. This grants no catalog listing, project administration, reservation, fine-tuning, IAM, or billing permission.

An operator can attach the generated policy to the existing instance role:

```bash
aws --profile ordertowork iam put-role-policy \
  --role-name ordertowork-hackathon-runtime \
  --policy-name OrderToWorkMantleInference \
  --policy-document file:///tmp/ordertowork-mantle-policy.json
```

This updates IAM only; it does not create provisioned throughput or another host. For the existing host, preserve the S3/ECR/SSM statements and remove the obsolete Nova invocation statement after Mantle is verified, retaining a reviewed rollback policy locally. For new hosts, `aws_budget_host.py` selects Mantle policy statements directly from the services manifest. Do not rerun host provisioning just to change this permission.

The worker generates temporary bearer tokens in memory from renewable instance-role credentials. Keep IMDSv2 required and the metadata response hop limit at 2 for Docker. No `OPENAI_API_KEY`, `AWS_BEARER_TOKEN_BEDROCK`, root credential, or CLI login file belongs in the host environment, image, release bundle or Git. The application uses the configured project through the OpenAI project header. These are AWS calls, so an OpenAI account/key is not required.

## Verification and rollback

After releasing, execute a bounded synthetic order analysis through the deployed job path. Confirm successful tool execution and structured output, persisted model/usage evidence, and a completed job. Check source evidence and incomplete requests still fail closed. Exercise approval and the production gate separately; a successful model greeting is insufficient.

Do not print generated bearer tokens or request authorization headers. Keep any failure evidence sanitized. IAM changes may take time to propagate; repeated unbounded inference retries are not an access diagnostic.

To stop new inference, set `OTW_MAX_DAILY_BEDROCK_ATTEMPTS=0` and recreate both API and worker. For independent cloud enforcement, remove or deny the Mantle inference permission. Changing only the environment variable back to `runtime` does not grant account access to the previously blocked runtime models.

Sources: [Mantle IAM actions and resources](https://docs.aws.amazon.com/service-authorization/latest/reference/list_bedrock-mantle.html), [API-key permissions and refresh](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html). Reviewed 11 September 2026.
