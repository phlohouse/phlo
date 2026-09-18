/**
 * Banner and EmptyState: inline feedback for degraded or blocked states.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { Banner } from "@/components/feedback/banner";
import { EmptyState } from "@/components/feedback/empty-state";
import { InlineLink } from "@/components/layout/section-header";
import { Button } from "@/components/ui/button";

const meta: Meta = { title: "Feedback/Banner" };
export default meta;

export const Tones: StoryObj = {
  render: () => (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, width: 760 }}>
      <Banner
        tone="danger"
        title="Next delivery blocked by a quality check"
        detail="Run r7e42b · 42 duplicate order IDs · Released data is unchanged."
        action={<InlineLink>Inspect run</InlineLink>}
      />
      <Banner
        tone="warning"
        title="Loki is running, but log queries are unavailable"
        detail="Readiness probe timed out at 09:35 UTC · Durable run evidence remains available."
        action={<InlineLink>Inspect dependency path</InlineLink>}
      />
      <Banner
        tone="info"
        title="Data release only"
        detail="Target delivery and Dataset publication are tracked separately."
      />
    </div>
  ),
};

export const WithoutAction: StoryObj = {
  render: () => (
    <div style={{ width: 760 }}>
      <Banner
        tone="danger"
        title="Polaris is unreachable — no response since 09:21 (14m)"
        detail="Evidence sourced from Polaris is stale and stamped “last confirmed”. Released data is unaffected: snapshot 938105 remains what consumers read. Nothing is assumed."
      />
    </div>
  ),
};

export const Empty: StoryObj = {
  render: () => (
    <div style={{ width: 620 }}>
      <EmptyState
        title="No pending releases"
        detail="Candidates appear here as soon as a run proposes a snapshot for promotion."
        action={<Button variant="outline">View release history</Button>}
      />
    </div>
  ),
};
