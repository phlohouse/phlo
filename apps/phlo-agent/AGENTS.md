# Phlo agent app

`apps/phlo-agent` is an independent Node 24 Eve application. `package.json` and `package-lock.json` own its dependency and command contract; `agent/instructions.md` owns the runtime agent instructions.

## Route the change

- Put channel adapters in `agent/channels/`, tool implementations in `agent/tools/`, extensions in `agent/extensions/`, and shared policy or service logic in `agent/lib/`.
- Keep repository-maintenance workflows in `agent/skills/` and scheduled prompts in `agent/schedules/`; update the runtime instructions only when agent behaviour itself changes.
- Add a colocated `*.test.ts` for deterministic policy, routing, trust, and GitHub behaviour. Tests run directly with Node's TypeScript stripping, so preserve ESM-compatible imports.
- Regenerate `package-lock.json` with npm when dependencies change.

## Complete the change

From `apps/phlo-agent`, run:

```bash
npm test
npm run typecheck
npm run build
```

The change is complete when all three commands pass, new deterministic behaviour has a focused test, and changes that affect a live channel or browser flow have been exercised through that boundary or the unverified boundary is reported.
