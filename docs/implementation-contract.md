# Development conventions

OrderToWork is a Python/FastAPI service and a React/TypeScript client. PostgreSQL owns business state. Strands coordinates read-only tools and produces a structured request interpretation; deterministic services price proposals and commit approved work.

## Layout

- `backend/ordertowork/api`: HTTP validation, authentication, response mapping and transaction boundaries.
- `backend/ordertowork/services`: domain operations, provider integrations and durable job scheduling.
- `backend/ordertowork/models`: SQLAlchemy persistence, split into identity, orders and operations.
- `backend/ordertowork/worker.py`: leased background analysis with bounded inference and stale-result rejection.
- `frontend/src`: typed API client, reusable interface components and pages.
- `migrations`: reviewed Alembic revisions; migrations are run once per release.
- `backend/tests`: deterministic, HTTP, SDK-contract and isolated PostgreSQL concurrency tests.

## Rules

Use integer cents for money, basis points for deposits, timezone-aware timestamps and explicit units for resources. Product/profile configuration supplies deterministic prices and requirements. Every business-owned operation checks the authenticated principal's membership and scope. Model-supplied IDs never grant permissions.

Persist customer messages separately from interpreted facts. Keep accepted revisions, pending proposals, payments, reservations and production holds distinct. Bind approval to immutable content and its base revision. Never hold database locks across model calls or human waits. Use bounded, idempotent operations and preserve the previous agreement when a replacement fails.

Customer links grant access only to their proposal. Protect their tokens as bearer credentials. Workspace owners and production operators have separate views; platform administration exposes operational metadata, not general business-content access.

Tests using SQLite establish deterministic behavior only. PostgreSQL concurrency checks use unique temporary schemas and run when `OTW_TEST_DATABASE_URL` is supplied. A scripted Strands model verifies SDK integration; only an actual account-configured Bedrock run establishes live model behavior.

Run relevant tests and lint before a milestone commit. Keep credentials, local state, dependencies, generated builds and recordings out of Git. See [HTTP contract](api-contract.md), [architecture](architecture.md), and [deployment](../deploy/README.md).
