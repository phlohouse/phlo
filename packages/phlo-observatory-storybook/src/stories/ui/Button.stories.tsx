/**
 * Button: every variant and control size.
 */
import type { Meta, StoryObj } from "@storybook/react";
import { Play, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";

const meta: Meta<typeof Button> = { title: "UI/Button", component: Button };
export default meta;

export const Variants: StoryObj = {
  render: () => (
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <Button>Create workflow</Button>
      <Button variant="outline">Last 24 hours</Button>
      <Button variant="secondary">Secondary</Button>
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
      <Button size="icon" aria-label="Create">
        <Plus />
      </Button>
      <Button size="icon-sm" aria-label="Run">
        <Play />
      </Button>
    </div>
  ),
};

export const WithIcons: StoryObj = {
  render: () => (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <Button>
        <Plus />
        Create workflow
      </Button>
      <Button variant="outline">
        <Play />
        Preview retry
      </Button>
    </div>
  ),
};

export const Disabled: StoryObj = {
  render: () => (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <Button disabled>Create workflow</Button>
      <Button variant="outline" disabled>
        Last 24 hours
      </Button>
    </div>
  ),
};
