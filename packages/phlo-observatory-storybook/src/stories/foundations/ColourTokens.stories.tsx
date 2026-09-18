/**
 * Colour token reference: every semantic token with its intended role.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { ColorTokenGrid } from "@/components/sections/reference/reference-blocks";

const meta: Meta = { title: "Foundations/Colour tokens" };
export default meta;

const TOKENS = [
  { name: "--background", usage: "App canvas behind the workspace card" },
  { name: "--card", usage: "Workspace card and section surfaces" },
  { name: "--popover", usage: "Menus, palettes, popovers" },
  { name: "--foreground", usage: "Primary text" },
  { name: "--muted-foreground", usage: "Secondary text, labels, meta" },
  { name: "--border", usage: "Hairline separators and card outlines" },
  { name: "--input", usage: "Form control outlines" },
  { name: "--ring", usage: "Focus ring" },
  { name: "--primary", usage: "Primary action fill, brand violet" },
  { name: "--primary-strong", usage: "Accent text and links" },
  { name: "--accent", usage: "Table header band, active navigation" },
  { name: "--secondary", usage: "Attention rows and quiet fills" },
  { name: "--subtle", usage: "Metric strip and inset surfaces" },
  { name: "--muted", usage: "Neutral fills" },
  { name: "--success", usage: "Ready, passed, connected" },
  { name: "--success-soft", usage: "Success badge fill" },
  { name: "--warning", usage: "Delayed, blocked, degraded" },
  { name: "--warning-soft", usage: "Warning badge fill" },
  { name: "--warning-surface", usage: "Degraded row and banner fill" },
  { name: "--destructive", usage: "Failed, drift, unreachable" },
  { name: "--destructive-soft", usage: "Failure badge fill" },
  { name: "--destructive-surface", usage: "Blocked banner fill" },
  { name: "--destructive-border", usage: "Blocked banner outline" },
];

export const Palette: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 1000 }}>
      <ColorTokenGrid tokens={TOKENS} />
    </div>
  ),
};

export const DarkPalette: StoryObj = {
  globals: { theme: "dark" },
  render: () => (
    <div style={{ maxWidth: 1000 }}>
      <ColorTokenGrid tokens={TOKENS} />
    </div>
  ),
};
