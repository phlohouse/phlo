/**
 * Documentation content for "How to read Mission Control".
 *
 * Kept as data so the route stays a pure renderer and the copy can be reviewed
 * or localised without touching component code.
 */
export interface DocCard {
  title: string;
  body: string;
  seen?: string;
  tone?: string;
}

export interface DocGroup {
  title: string;
  intro?: string;
  cards: Array<DocCard>;
}

export const documentationGroups: Array<DocGroup> = [
  {
    title: "Three states, kept separate on every screen",
    intro:
      "A run can fail while its evidence is complete and the release untouched — all three are always shown, never merged",
    cards: [
      {
        title: "Execution state",
        body: "What the run actually did: Running, Succeeded, Failed. Reported by the orchestrator.",
        seen: "Seen on: Runs header · run rows · activity",
      },
      {
        title: "Evidence state",
        body: "What can be proven right now: Complete, Partial, Stale. Reported by providers — it can lag reality.",
        seen: "Seen on: Runs header · quality checks · banners",
      },
      {
        title: "Release state",
        body: "What consumers can read: Released, Pending, Blocked, Not promoted. Only changes through promotion.",
        seen: "Seen on: Releases · dataset header · run header",
      },
    ],
  },
  {
    title: "Two kinds of data",
    cards: [
      {
        title: "Candidate",
        body: "Data a run produced and proposed for release — e.g. snapshot 938106. Inspected, checked, and either promoted or discarded. A failed candidate never touches what consumers read.",
      },
      {
        title: "Released",
        body: "The snapshot consumers actually resolve to — e.g. 938105. It stays valid even while new evidence about it is stale, and even while a candidate behind it fails.",
      },
      {
        title: "Publication vs release",
        body: "Publishing a Dataset makes it named and discoverable; releasing promotes a snapshot to readers. Publication reviews approve the first — they never move data.",
      },
    ],
  },
  {
    title: "Service and access distinctions",
    cards: [
      {
        title: "Running vs ready",
        body: "Running means the process is alive; ready means it can serve its function. Loki runs but is not ready — a dependency in its path is down.",
      },
      {
        title: "Declared → compiled → verified",
        body: "Policy files declare intent, the engine compiles grants, the backend verifies what it actually enforces. Drift is when verified ≠ compiled — like an unexpected UPDATE grant.",
      },
      {
        title: "Provider reachability",
        body: "Whether a catalog or store answers health checks — a property of the connection, not of the data. Managed in Settings → Provider connections.",
      },
    ],
  },
  {
    title: "Outcomes you will see",
    intro: "Unknown is a state, not an error — it means we could not confirm, not that it failed",
    cards: [
      { title: "Failed", body: "A confirmed failure — the step ran and lost.", tone: "text-destructive" },
      { title: "Blocked", body: "Waiting on something named — a check, a review, a dependency.", tone: "text-warning" },
      { title: "Unknown", body: "Applied but unconfirmed. Reconcile — never blind-retry.", tone: "text-accent-foreground" },
      { title: "Incomplete", body: "Partial evidence — some checks never reported.", tone: "text-muted-foreground" },
      { title: "Not started", body: "Not yet attempted — distinct from failed or blocked.", tone: "text-muted-foreground" },
    ],
  },
  {
    title: "Stale evidence and “last confirmed”",
    cards: [
      {
        title: "“Last confirmed 09:21”",
        body: "The figure is the last truth the provider confirmed — shown honestly, never refreshed optimistically. It is not a guess and not a live read.",
      },
      {
        title: "Released data stays valid",
        body: "Stale evidence means we cannot re-verify — it does not mean the data changed. “Released data unchanged” can be true while every freshness figure is stale.",
      },
      {
        title: "What is assumed: nothing",
        body: "When a provider is down, affected surfaces say so and carry the timestamp. Figures are never silently extrapolated.",
      },
    ],
  },
  {
    title: "What you can safely do",
    intro:
      "Every action follows the same contract — applying submits the operation, it does not promise success",
    cards: [
      { title: "1 · Preview", body: "Preview-verbed actions show the plan before anything happens." },
      { title: "2 · Review", body: "The plan diff, required permission, and what will change." },
      { title: "3 · Apply", body: "Submits with an idempotency key — safe to re-request, never a duplicate effect." },
      { title: "4 · Observe", body: "Watch the operation run. Nothing is released while observing." },
      { title: "5 · Reconcile", body: "If the outcome can't be confirmed, reconcile against the provider's own record." },
    ],
  },
];
