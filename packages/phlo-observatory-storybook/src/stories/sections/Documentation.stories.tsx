/**
 * Documentation section: a group of explainer cards.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { DocGroupSection } from "@/components/sections/documentation/doc-group";
import { documentationGroups } from "@/content/documentation";

const meta: Meta = { title: "Sections/Documentation" };
export default meta;

export const ThreeStates: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 980 }}>
      <DocGroupSection group={documentationGroups[0]!} />
    </div>
  ),
};

export const Outcomes: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 980 }}>
      <DocGroupSection group={documentationGroups[3]!} />
    </div>
  ),
};

export const AllGroups: StoryObj = {
  render: () => (
    <div style={{ display: "flex", flexDirection: "column", gap: 20, maxWidth: 980 }}>
      {documentationGroups.map((group) => (
        <DocGroupSection key={group.title} group={group} />
      ))}
    </div>
  ),
};
