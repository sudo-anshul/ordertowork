# Local development


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

## Checks


```sh
OTW_TEST_DATABASE_URL=postgresql+psycopg://ordertowork:ordertowork@localhost:55432/ordertowork sh scripts/check.sh
uv run alembic check
npm --prefix frontend audit
```

PostgreSQL tests use temporary schemas and clean up their own data. Without `OTW_TEST_DATABASE_URL`, those checks skip; other tests use isolated SQLite databases. Coverage includes tenant and role boundaries, Cognito token validation with mocked provider calls, consent and stale revisions, approval races, job leases, deposits, file authorization and a scripted Strands tool round-trip. Tests do not establish live model accuracy or a production security audit.

GitHub Actions runs backend checks with PostgreSQL, the frontend build and a complete container build. See [validation](validation.md) for observed results.


See the [project overview](../README.md), [guided walkthrough](walkthrough.md), and [deployment runbook](../deploy/README.md).
