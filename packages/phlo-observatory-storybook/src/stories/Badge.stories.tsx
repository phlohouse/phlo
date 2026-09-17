/**
 * Badge stories: tones and status pill mapping.
 */
import type { Meta, StoryObj } from "@storybook/react";
import { Badge } from "../../../phlo-observatory/web/src/components/ui/badge";

const meta: Meta = { title: "UI/Badge" };
export default meta;

export const Tones: StoryObj = {
  render: () => (
    <div style={{ display: "flex", gap: 8 }}>
      <Badge tone="success">Available</Badge>
      <Badge tone="warning">Review</Badge>
      <Badge tone="destructive">Flag</Badge>
      <Badge tone="violet">Analysis</Badge>
    </div>
  ),
};
export const StatusPill: StoryObj = { render: () => <Badge tone="primary">In progress</Badge> };
