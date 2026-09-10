# Implementation verification

Local verification was expanded on 10 September 2026. Development uses PostgreSQL 17, a real FastAPI service and worker, and the React interface. AWS service configuration is now available; see the separate [live provider verification procedure](live-aws-checks.md). The checks below do not establish Bedrock access or model accuracy.

## Automated application checks

- **94 backend tests passed** with `OTW_TEST_DATABASE_URL` configured.
- Seven of these use actual PostgreSQL: a competing-approval race, three scheduler/lease concurrency cases, an atomic global paid-run budget race, and complete HTTP-to-worker-to-production flows for both business profiles. They use temporary schemas and remove their own test data.
- Remaining tests use isolated databases and cover identity, CSRF/origin checks, tenant and role boundaries, final-owner protection, stale proposals, exact approval, manual-deposit idempotency, production gates, attachment access and conservative reference interpretation.
- The Strands SDK integration test uses a scripted model with real application tools and structured output. This verifies the SDK/tool contract, not Bedrock access or model accuracy.
- Added checks cover bounded Strands turns/tokens, independent session snapshots, exact final previews, partial usage telemetry, live-provider error handling, SigV4 storage contracts and actual Cognito HTTP request serialization with mocked provider transport.
- Ruff passed for backend, migrations, scripts and deployment code. Alembic reports no schema drift against the migrated PostgreSQL database.
- The strict TypeScript/Vite production build passed. The full npm audit reported zero known vulnerabilities at verification time.

The test suite emits one upstream Starlette/AnyIO deprecation warning. No test failures were observed.

## Browser acceptance

The following checks used the running app and persisted PostgreSQL data; workflow responses were not mocked:

| Flow | Observed result |
|---|---|
| Owner setup | Development identity, workspace creation, profile selection and labeled sample orders work. |
| Message analysis | Saving the merchandise change request queues a real worker job, which completes in reference mode and persists new proposals. |
| Price and constraints | The 45-shirt navy/Thursday request reports both stock and capacity shortages; the charcoal/Friday option totals $810 with $135 deposit top-up. |
| Exact customer consent | Submitting without checking consent does not approve. Explicit approval produces a receipt that survives reload. |
| Production gating | After revised approval, the owner sees production blocked for the $135 deposit remainder. Recording a labeled sample deposit releases the current ticket. |
| Production start | Start confirmation succeeds for the reviewed revision; the in-production state persists after reload. Automated tests separately reject stale ticket starts. |
| Private files | Owner uploads and downloads a PNG through the UI; original and downloaded SHA-256 hashes match. |
| Replaced review link | A newer share link revokes its predecessor; opening the old link displays an unavailable-link state. |
| Customer change request | Customer reply is recorded, preserves the existing agreement, and revokes the previous proposal link. |
| New bakery order | Six items price at $24 with a $12 required deposit. Pickup is preserved in the business timezone, independently of the browser timezone. |
| Operator handoff | An existing test operator was added through Team. The operator sees production navigation and a ticket without financial records, customer email or source messages. Direct access to full orders returns 403; starting the reviewed ticket succeeds. |
| Platform support | An explicitly allowlisted local admin sees counts matching PostgreSQL and can navigate and sign out without owning a workspace. An ordinary account is denied by both the interface and the API. Support responses contain operational metadata only. |
| Execution evidence | A completed worker job displays its two recorded reference events, checked price, stock/capacity shortages and quoted source message. |
| Responsive and keyboard interaction | Setup and customer review fit 320px and 390px viewports without document overflow. Consent and approval work with Space, Tab and Enter. |

Sample orders and local development identities are synthetic. Browser checks are targeted acceptance evidence, not exhaustive device, accessibility or load testing.

## Runtime package

The one-command development launcher was started, checked through the UI's API proxy, and stopped with Ctrl+C. Both application ports closed, the launcher exited successfully, and PostgreSQL data remained available. The app was then restarted with the same command.

The complete Linux ARM64 image was built and exercised as a non-root user with a read-only root filesystem, dropped capabilities and no-new-privileges. Checks cover static assets, SPA deep links, security headers, API health/readiness, anonymous access rejection, disabled production development-login, worker CLI and the migration head. Readiness fails with 503 when the database is unavailable while process health stays 200.

See [container evidence](../deploy/validation.md) and the reproducible [smoke script](../deploy/smoke_image.py). Hosted CI status is available in the [GitHub Actions runs](https://github.com/sudo-anshul/ordertowork/actions); it must be checked for the specific commit being deployed.

## Pending live validation

The initial local implementation left Cognito login, permitted Bedrock inference, private S3 round-trips, deployed networking/IAM and backup recovery pending. Record subsequent live checks separately with the deployment release. Reference mode and mocked provider tests cannot establish those results. Bedrock account verification and real customer feedback must not be inferred from a passing infrastructure health check.
