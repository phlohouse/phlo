/**
 * Button stories: variants and control sizes from the Paper scale.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { Button } from "@/components/ui/button";

const meta: Meta<typeof Button> = { title: "UI/Button", component: Button };
export default meta;

export const Variants: StoryObj = {
  render: () => (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <Button>Create workflow</Button>
      <Button variant="outline">Last 24 hours</Button>
      <Button variant="ghost">Cancel</Button>
      <Button variant="destructive">Delete</Button>
      <Button variant="link">View all</Button>
    </div>
  ),
};

export const Sizes: StoryObj = {
  render: () => (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <Button size="sm">Small</Button>
      <Button>Default</Button>
      <Button size="lg">Large</Button>
    </div>
  ),
};
