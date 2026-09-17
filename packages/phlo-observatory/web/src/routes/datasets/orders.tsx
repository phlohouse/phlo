/**
 * Dataset detail placeholder pending Stage 1 Dataset build.
 */
import { createFileRoute } from "@tanstack/react-router";

function Stub() {
  return (
    <div>
      <h1 style={{ fontSize: 24, fontWeight: 750, margin: 0 }}>Data</h1>
      <p style={{ fontSize: 13, color: "var(--color-muted)" }}>Orders · Stage 1 build in progress.</p>
    </div>
  );
}

export const Route = createFileRoute("/datasets/orders")({ component: Stub });
