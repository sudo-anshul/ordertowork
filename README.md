# OrderToWork

A workbench for made-to-order businesses. A Strands agent interprets customer requests, checks business rules, and prepares feasible revisions. Customers approve exact terms; transactional application code controls reservations and production release.

Implementation is in progress. The repository will be updated at verified milestones. Synthetic sample orders and deterministic reference mode are explicitly labeled. Reference mode does not call an AI model.

## Local development

Requirements: Python 3.12, uv, Node.js 22+, Docker.

```sh
cp .env.example .env
uv sync --all-groups
docker compose up -d db
uv run uvicorn ordertowork.main:app --reload --host 127.0.0.1 --port 8000 --no-access-log
```

`GET /api/health` checks the API; `GET /api/ready` checks database connectivity. Database migrations, worker and UI commands will land with their respective milestones.

## Checks

```sh
uv run pytest
uv run ruff check backend
```

The implementation uses managed Cognito authentication in production. Development identity is local-only and must never be exposed publicly. No AWS credentials or customer data belong in this repository.

See [implementation contract](docs/implementation-contract.md) and [HTTP contract](docs/api-contract.md). MIT licensed.
