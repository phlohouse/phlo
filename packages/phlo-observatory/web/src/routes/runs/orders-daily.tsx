/**
 * Run detail placeholder pending Stage 1 Runs build.
 */
import { createFileRoute } from "@tanstack/react-router";

function Stub() {
  return (
    <div>
      <h1 style={{ fontSize: 24, fontWeight: 750, margin: 0 }}>Runs</h1>
      <p style={{ fontSize: 13, color: "var(--color-muted)" }}>Orders daily · Stage 1 build in progress.</p>
    </div>
  );
}

export const Route = createFileRoute("/runs/orders-daily")({ component: Stub });
