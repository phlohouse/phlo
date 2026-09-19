# Phlo Observatory

Operator console for a configured Phlo lakehouse: dataset inspection, run
diagnosis, guarded releases, and service operations backed by `phlo-api`.

- `src/phlo_observatory/` — Python service package (compose/service definition,
  plugin entry point).
- `web/` — TanStack Start application built into the `observatory` service
  image; production serving runs `serve.mjs` (srvx) against `dist/`.

See `docs/observatory/` for the operator runbook and acceptance evidence.
