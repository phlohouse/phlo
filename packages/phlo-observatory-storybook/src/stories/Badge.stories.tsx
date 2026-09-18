/**
 * Badge and StatusPill stories, including the backend status vocabulary.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { StatusPill } from "@/components/status-pill";
import { Badge } from "@/components/ui/badge";

const meta: Meta = { title: "UI/Badge" };
export default meta;

export const Tones: StoryObj = {
  render: () => (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      <Badge variant="default">Default</Badge>
      <Badge variant="secondary">Secondary</Badge>
      <Badge variant="outline">Outline</Badge>
      <Badge variant="muted">Internal</Badge>
      <Badge variant="success">Ready</Badge>
      <Badge variant="warning">Delayed</Badge>
      <Badge variant="destructive">Failed</Badge>
      <Badge variant="accent">Review</Badge>
    </div>
  ),
};

export const StatusVocabulary: StoryObj = {
  render: () => (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", maxWidth: 720 }}>
      <StatusPill status="Ready" />
      <StatusPill status="Fresh" />
      <StatusPill status="Complete" />
      <StatusPill status="Published" />
      <StatusPill status="Pending" />
      <StatusPill status="Ready for review" />
      <StatusPill status="35m late" />
      <StatusPill status="Blocked by quality" />
      <StatusPill status="Failed validation" />
      <StatusPill status="Unreachable · timing out" />
      <StatusPill status="Not started" />
    </div>
  ),
};
