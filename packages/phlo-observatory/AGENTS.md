# Observatory

Observatory combines a Python service plugin in `src/phlo_observatory/*.py` with a TanStack Start application in `src/phlo_observatory/src/`. The inner `package.json`, `vite.config.ts`, and `vitest.config.ts` are authoritative for frontend commands and runtime configuration.

## Route the change

- Put Python plugin discovery, settings, manifests, and service wiring in `src/phlo_observatory/*.py`; cover it in `tests/`.
- Put browser routes in `src/phlo_observatory/src/routes/`, reusable Observatory views in `src/observatory/`, and server-only adapters in `src/server/`.
- Keep browser-safe API projections in `src/observatory/api/`; keep credentials and privileged mutations server-side.
- Treat `src/routeTree.gen.ts` as generated output. Change route source files and let the TanStack/Vite tooling regenerate the tree.
- Regenerate the inner `package-lock.json` with npm when frontend dependencies change.

## Complete the change

Run Python tests from the repository root:

```bash
uv run --locked --package phlo-observatory --with pytest pytest -m "not integration" --tb=short packages/phlo-observatory/tests
```

Run frontend checks from `packages/phlo-observatory/src/phlo_observatory`:

```bash
npm test
npm run typecheck
npm run lint
npx prettier --check .
npm run build
```

For a visible UI change, also run the app, inspect every affected state at a representative desktop viewport, and capture reviewable before/after evidence. The change is complete when focused Python and frontend checks pass, the production bundle builds, and visual behaviour has been inspected rather than inferred from unit tests.
