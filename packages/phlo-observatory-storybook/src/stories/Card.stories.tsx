/**
 * Card stories: the 7px section block and 12px workspace panel.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const meta: Meta = { title: "UI/Card" };
export default meta;

export const SectionBlock: StoryObj = {
  render: () => (
    <div style={{ width: 420 }}>
      <Card>
        <CardHeader>
          <CardTitle>Needs attention</CardTitle>
          <span style={{ fontSize: 11, color: "var(--muted-foreground)" }}>3 items</span>
        </CardHeader>
        <CardContent className="border-t border-border px-3 py-2 text-xs">
          Orders delivery blocked · 42 duplicate IDs
        </CardContent>
      </Card>
    </div>
  ),
};

export const Panel: StoryObj = {
  render: () => (
    <div style={{ width: 420 }}>
      <Card variant="panel">
        <CardHeader>
          <CardTitle>Workspace panel</CardTitle>
        </CardHeader>
        <CardContent className="px-3 pb-3 text-xs text-muted-foreground">
          The 12px surface that frames every screen.
        </CardContent>
      </Card>
    </div>
  ),
};
