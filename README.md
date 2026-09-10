# OrderToWork

A workbench for made-to-order businesses. Turn a customer request into a checked proposal, an exact agreement, and a production ticket the team can trust.

![OrderToWork workbench with a labeled sample order](docs/screenshots/workbench.png)

OrderToWork keeps the existing commitment intact while a change is explored. A Strands agent interprets the message and calls scoped tools for order facts, prices, stock and capacity. The application rechecks availability under database locks when the customer approves. An agent cannot accept an order, take money or release production.

The complete local app runs without AWS credentials. Its default **reference mode is a limited deterministic interpreter, not live AI**. The real Strands/Bedrock path is implemented and covered with a scripted model integration test; live Bedrock, Cognito and S3 validation requires an AWS account and is still pending.

## What works

- Owner workbench, order register, message intake and durable analysis jobs.
- Immutable proposals, price and resource checks, and private expiring customer review links.
- Explicit customer consent, change requests, stale-link handling and atomic reservation replacement.
- Manual deposit records, production holds, revision-bound tickets and production start.
- Separate owner and operator permissions, membership management and platform support metadata.
- Private PNG/JPEG/PDF attachments for owners, with size and content-type checks.
- Configurable merchandise and bakery profiles, including prices, variants, stock, dated capacity and deposit requirements.

Samples are clearly labeled. Real workspaces begin with zero availability and empty production specifications. The two profiles demonstrate a shared workflow; this release does not claim to support every business process.

## Local development

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 22+ and a running Docker engine.

From the repository root:

```sh
python3 scripts/dev.py
```

The launcher creates `.env` from `.env.example` if absent, installs dependencies, starts local PostgreSQL, applies migrations, and runs the API, worker and Vite. Open **http://localhost:5173**. Use a test identity on the development sign-in screen, create a workspace, and select sample data to try a prepared scenario. Development sign-in is not verified identity and is restricted to loopback; never expose it publicly or enter real credentials into it.

Ctrl+C stops the application processes. PostgreSQL data stays in its Docker volume, and private local files stay in `.data/`. Run `docker compose stop db` to stop the database while retaining its data.

For separate terminals:

```sh
cp .env.example .env  # First setup only; preserve an existing .env.
uv sync --locked
npm --prefix frontend ci
docker compose up -d --wait db
uv run alembic upgrade head

# Terminal 1
uv run uvicorn ordertowork.main:app --reload --host 127.0.0.1 --port 8000 --no-access-log
# Terminal 2
uv run python -m ordertowork.worker
# Terminal 3
npm --prefix frontend run dev
```

Ports: UI 5173, API 8000, PostgreSQL 55432. The UI proxies `/api` to FastAPI. `GET /api/health` checks the process, `/api/ready` checks the database connection, and `/api/runtime` identifies the configured execution mode. Interactive API documentation is at http://localhost:8000/api/docs.

## Try the full workflow

1. Create a merchandise workspace with sample data. Open **OT-1048 / Field Notes Club**.
2. Review the request to add 15 medium shirts and move pickup earlier. The original order is 30 navy shirts for $540; navy stock and Thursday capacity prevent the requested combination.
3. To exercise the actual worker, use **Add customer message** and paste the sample request. Wait for the saved analysis.
4. Share the option for 45 charcoal shirts at the original Friday pickup: $810, with $135 additional deposit. Sharing alone does not reserve the new resources.
5. Open the customer view, review the differences, check consent and approve. Production remains blocked until the required deposit is recorded.
6. For this sample only, record the $135 deposit with a clearly labeled test reference. View the accepted work order and start production.

Revision numbers depend on how many proposals have been prepared. A second bakery sample exercises the same rules with batch capacity measured in minutes. Create another sample workspace to repeat a scenario; started work is intentionally not reset automatically.

## Checks

```sh
OTW_TEST_DATABASE_URL=postgresql+psycopg://ordertowork:ordertowork@localhost:55432/ordertowork sh scripts/check.sh
uv run alembic check
npm --prefix frontend audit
```

PostgreSQL tests use temporary schemas and clean up their own data. Without `OTW_TEST_DATABASE_URL`, those checks skip; other tests use isolated SQLite databases. Coverage includes tenant and role boundaries, Cognito token validation with mocked provider calls, consent and stale revisions, approval races, job leases, deposits, file authorization and a scripted Strands tool round-trip. Tests do not establish live model accuracy or a production security audit.

GitHub Actions runs backend checks with PostgreSQL, the frontend build and a complete container build. See [validation](docs/validation.md) for observed results.

## AWS activation and deployment

The app includes a non-root production Docker image, Cognito OAuth code/PKCE sign-in, Bedrock through Strands, private S3 storage, IAM examples and an [AWS deployment runbook](deploy/README.md). The same image runs the API and the worker as separate services. Production serves the built React app from FastAPI behind an HTTPS endpoint.

For the $50 hackathon allowance, use the [single-host deployment](deploy/budget-README.md) and [AWS service setup](docs/aws-services.md). They provide Cognito, private S3, an account cost budget and one small ARM server running PostgreSQL, the API and the worker. The managed ECS/RDS topology is a later option with substantially higher idle costs.

Live verification is documented separately from configuration in [AWS checks](docs/live-aws-checks.md). Bedrock requires AWS account verification and model access even after credits are redeemed. A failed model call remains a failed analysis; the app does not silently replace it with reference interpretation.

Paid jobs have a global daily attempt allowance across all workspaces, including retries. `OTW_MAX_DAILY_BEDROCK_ATTEMPTS=0` pauses paid work. Per-run output, turn and token limits reduce exposure; the token limit is checked between turns and is not an AWS dollar spending cap. The execution record displays reported input/output tokens.

Never commit credentials, `.env`, `.data`, database dumps or real customer files. The repository ignores local secrets and runtime data.

## Release boundaries

Deposits record money received elsewhere; no payments or refunds are processed. Customer links are private bearer capabilities, not verified customer accounts. Outbound email/WhatsApp, document extraction, malware scanning, ingredient-level recipe planning, automated data erasure and a general profile builder are outside this release. Operators see approved production facts but do not have owner attachment access. Application queries enforce tenant isolation; PostgreSQL row-level security is not enabled.

See [architecture](docs/architecture.md), [development conventions](docs/implementation-contract.md) and [HTTP contract](docs/api-contract.md). MIT licensed.
