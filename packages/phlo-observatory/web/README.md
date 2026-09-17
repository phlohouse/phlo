# Phlo Observatory Web (clean rewrite)

React 19 + TanStack Start + Tailwind v4. UI-only scaffold reading `src/data/demo.ts`.

## Dev

```bash
npm install
npm run dev -- --port 3001
```

## Routes (25)

`/`, `/projects`, `/projects/$projectId`, `/notebook/$experimentId`,
`/analysis`, `/sheets/$sheetId`, `/plates/$designId`, `/samples`,
`/reagents`, `/datasets/$datasetId`, `/schemas`, `/storage`,
`/templates`, `/search`, `/reviews`, `/calendar`, `/planner`,
`/instruments`, `/lineage`, `/reports/$reportId`, `/pipelines`,
`/agents`, `/integrations`, `/admin`, `/settings`

## Demo data

`src/data/demo.ts` mocks future phlo-api responses. No fetching, no
mutations. Replace imports with query hooks when API lands — see
`../API_AUDIT.md` for endpoint gaps.

## Tokens

Paper Sheetbase V2ALT CSS vars live in `src/styles.css`.
