# Deployment-package verification

These checks were performed locally while implementing the package. They do not establish a working AWS deployment.

| Check | Result |
|---|---|
| Backend Docker stage | Passed: `docker build --target backend --tag ordertowork-backend:local .` |
| Dockerfile checks | Passed: `docker build --check .`, no warnings. |
| Container backend runtime | Build used CPython 3.12.14 on Linux ARM64, uv 0.11.7 and the frozen production lockfile; application wheel installed successfully. |
| IAM example JSON | All four files parse as JSON and contain IAM policy version/statement fields. Permissions have not been evaluated by IAM Access Analyzer or an AWS principal. |
| GitHub Actions YAML | Parsed locally; backend/frontend/container jobs and PostgreSQL port mapping verified. Check the repository's Actions result for the exact release commit. |
| RDS CA bundle | Retrieved over verified HTTPS from AWS's public RDS trust store and parsed as a PEM certificate bundle by OpenSSL. |
| Full frontend/container | Passed: `docker build --tag ordertowork:local .`; Node 22 ran `npm ci` and the strict TypeScript/Vite production build successfully. The install reported zero known npm vulnerabilities at build time. |
| Non-root runtime | Passed as UID 10001 with a read-only root filesystem, dropped Linux capabilities and no-new-privileges. |
| HTTP and bundled frontend | API health 200, PostgreSQL-backed readiness 200, root/deep-link SPA responses, built JS/CSS assets, production security headers and correct unknown-route/asset 404 responses. |
| Authentication boundaries | Anonymous `/api/auth/me` returned 401; development login returned 404 under production configuration. No identity-provider login was attempted. |
| Packaged operational commands | `uv run python -m ordertowork.worker --help` and `uv run alembic current` succeeded as the non-root image user; the existing local database reported migration `3edc2a397b7c (head)`. No migration or application data mutation was performed by this check. |
| Readiness failure | A separate network-isolated process returned health 200 and readiness 503 when PostgreSQL was unreachable. |
| Access-log policy | Both container stdout and stderr inspected after HTTP probes; no request access logs emitted. |

The final image was tested on **Linux ARM64**, with image ID `sha256:2d95bae793ab561b19165762904dcd6cf1b517529d7af7b56d493db0b5914335`. No Linux x86 image or live AWS runtime was tested locally. Rebuild and rerun the checks after source changes; an earlier image does not verify later edits.

Reproducible smoke check: `python deploy/smoke_image.py` against the existing local Compose database. Exact results are in `validation-smoke.json`. Its temporary containers were removed after execution.

The public CA bundle SHA-256 is `e5bb2084ccf45087bda1c9bffdea0eb15ee67f0b91646106e466714f9de3c7e3`. Source: <https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem>.

Cognito token exchange against AWS, real Bedrock model access, private S3 access, RDS connectivity, ECS deployment, IAM policy behavior, billing and backup restoration remain unverified until the owner supplies AWS access and authorizes the relevant resources. The application authentication suite separately exercises generated RSA-signed identity tokens and mocked provider exchange; those tests are not a live Cognito login.
