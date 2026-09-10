# OrderToWork frontend

React, TypeScript, Vite, React Router, Lucide icons, and Luxon. The Paper Workbench interface uses local system fonts and the FastAPI backend for all workflow state.

```sh
npm ci
npm run dev
npm run build
```

Development binds to `127.0.0.1:5173` with a strict port and proxies `/api` to `http://localhost:8000`. Set the backend application URL to the browser origin used for the frontend. Production output is `dist/`.

## Structure

- `src/lib/api.ts`: cookie credentials, CSRF header, structured API errors, mutation helpers.
- `src/lib/auth.tsx`: backend identity and workspace membership.
- `src/lib/hooks.ts`: cancellable loading, refresh, and mutation states.
- `src/lib/types.ts`: the domain, customer, team, and operator API projections.
- `src/components`: shared layout, order comparison, forms, attachments, and UI primitives.
- `src/pages`: login/setup, decisions/orders, exact-revision sharing and approval, resources/settings, team, platform metadata, and production.

Owners can work with the full order record. Operators use the dedicated production API and operational tickets; owner routes redirect to production. The backend independently enforces permissions. Public customer pages show only the public proposal projection and submit the displayed terms hash with explicit consent.

An analysis request saves its source and polls the persisted job. Reference mode is visibly labeled. Sharing creates a private review link, not an outgoing message. Deposit recording logs money already received; it does not capture a payment. Production start submits the revision shown when the confirmation was opened, allowing the backend to reject stale tickets.

Sample workspaces and orders are labeled. The frontend contains no local simulated order transitions. File uploads are stored as attachments and do not claim document extraction. Failed operations preserve entered form values and display the server error.

## Verification

`npm run build` performs strict TypeScript checking and creates the production bundle. Backend invariants and role isolation are covered by the repository's backend tests. The root integration workflow performs browser acceptance checks against the running API; a successful build alone is not a claim of complete browser or accessibility coverage.
