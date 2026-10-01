# Run the replacement Observatory

This frontend belongs to the `phlo-observatory` package in the Phlo repository.
It uses the public Phlo `/api/v1` contract. It does not query Dagster, Nessie,
Trino, or PostgreSQL directly.

From `packages/phlo-observatory/src/phlo_observatory`, install the shared lockfile
and run the replacement:

```sh
npm ci
PHLO_API_URL=http://127.0.0.1:4000 OBSERVATORY_UI=replacement sh select-ui.sh npm run dev
```

For a production Node server, build the workspace first:

```sh
npm run build --workspace replacement
PHLO_API_URL=http://127.0.0.1:4000 OBSERVATORY_UI=replacement sh select-ui.sh npm start
```

`PHLO_API_URL` must be reachable from the server. Protected API calls need the
user's bearer token in `Authorization` or `x-auth-request-access-token` from
the authenticated proxy. The server forwards that token to Phlo API, which
verifies it independently. Do not configure provider or database credentials
in this frontend. Never expose a bypass around the authenticated proxy.

With Phlo services, set `OBSERVATORY_UI=replacement` in the project's environment
and rebuild the existing `observatory` service. The image contains both UIs.
Unset the variable or set it to `legacy` to retain the old UI. Switching the UI
does not migrate data or retire legacy consumers.

`GET /healthz` returns `{"status":"ok"}` and `HEAD /healthz` returns 200.
This checks frontend liveness, not API connectivity, authentication, or readiness.
An unavailable API appears as an error in the relevant screen.

## Verify the app

Run these commands from the parent frontend directory:

```sh
npm run build --workspace replacement
npm run typecheck --workspace replacement
npx eslint src replacement/src --ext .ts,.tsx
npx prettier --check .
```

For live contract and isolation checks, run the following command from this
workspace against the documented disposable commerce fixtures. Supply
`PHLO_TEST_TOKEN` privately and set `PHLO_TEST_API_URL` to the real API endpoint.
The two numbers are independently known prod and staging asset counts.

```sh
node scripts/verify-api-client.mjs <app-url> 1 2
```

The script checks mounted OpenAPI route shapes, real pages, exact Iceberg IDs,
and asymmetric prod and staging inventories. It requires `agent-browser`.
It does not prove every method or response schema in OpenAPI matches the client.
