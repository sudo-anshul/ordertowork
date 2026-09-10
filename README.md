# OrderToWork

A workbench for made-to-order businesses. A Strands agent interprets customer requests, checks business rules, and prepares feasible revisions. Customers approve exact terms; transactional application code controls reservations and production release.

The backend implements identity, workspaces, order revisions, approval links, reservations, manual deposits, private files and a durable analysis worker. The interface is being integrated next. Synthetic sample orders and deterministic reference mode are explicitly labeled. Reference mode does not call an AI model.

## Local development

Requirements: Python 3.12, uv, Node.js 22+, Docker.

```sh
cp .env.example .env
uv sync --all-groups
docker compose up -d db
uv run alembic upgrade head
uv run uvicorn ordertowork.main:app --reload --host 127.0.0.1 --port 8000 --no-access-log
```

`GET /api/health` checks the API; `GET /api/ready` checks database connectivity. Run `uv run python -m ordertowork.worker` in a second terminal to process analysis jobs. PostgreSQL owns durable application state; the worker resumes queued work after restart.

## Checks

```sh
uv run pytest
uv run ruff check backend migrations
OTW_TEST_DATABASE_URL=postgresql+psycopg://ordertowork:ordertowork@localhost:55432/ordertowork uv run pytest -m postgres
```

The implementation uses managed Cognito authentication in production. Development identity is local-only and must never be exposed publicly. No AWS credentials or customer data belong in this repository.

See [architecture](docs/architecture.md), [development conventions](docs/implementation-contract.md) and [HTTP contract](docs/api-contract.md). MIT licensed.
