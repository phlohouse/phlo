/**
 * StatusPill and Badge: every tone plus the backend status vocabulary that
 * maps onto them.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { StatusPill } from "@/components/data/status-pill";
import { Badge } from "@/components/ui/badge";

const meta: Meta = { title: "Data/StatusPill" };
export default meta;

export const BadgeTones: StoryObj = {
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
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", maxWidth: 640 }}>
      {[
        "Ready",
        "Connected · 42ms",
        "Fresh",
        "Complete",
        "Published",
        "In sync",
        "Pending",
        "Ready for review",
        "Draft",
        "35m late",
        "Delayed",
        "Blocked by quality",
        "Failed validation",
        "Unreachable · timing out",
        "Drift detected",
        "Not started",
      ].map((status) => (
        <StatusPill key={status} status={status} />
      ))}
    </div>
  ),
};

export const ExplicitTones: StoryObj = {
  render: () => (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      <StatusPill tone="success">success</StatusPill>
      <StatusPill tone="warning">warning</StatusPill>
      <StatusPill tone="danger">danger</StatusPill>
      <StatusPill tone="accent">accent</StatusPill>
      <StatusPill tone="muted">muted</StatusPill>
      <StatusPill tone="muted" dot={false}>
        no dot
      </StatusPill>
    </div>
  ),
};
