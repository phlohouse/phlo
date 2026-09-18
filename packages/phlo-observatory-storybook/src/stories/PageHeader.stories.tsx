/**
 * PageHeader stories: title with accessory chips and trailing actions.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { ExampleDataChip, PageHeader } from "@/components/page-header";
import { StatusPill } from "@/components/status-pill";
import { Button } from "@/components/ui/button";

const meta: Meta = { title: "Domain/PageHeader" };
export default meta;

export const Overview: StoryObj = {
  render: () => (
    <div style={{ width: 900 }}>
      <PageHeader
        title="Overview"
        titleAccessory={<ExampleDataChip />}
        description="Monday, 14 September 2026"
        actions={
          <>
            <Button variant="outline">Last 24 hours</Button>
            <Button>Create workflow</Button>
          </>
        }
      />
    </div>
  ),
};

export const WithStatus: StoryObj = {
  render: () => (
    <div style={{ width: 900 }}>
      <PageHeader
        title="orders_daily"
        titleAccessory={
          <>
            <StatusPill tone="danger" dot={false}>
              Failed validation
            </StatusPill>
            <ExampleDataChip />
          </>
        }
        description="Run r7e42b · 13 Sep 2026, 09:25 UTC · Scheduled · Partition 2026-09-13"
      />
    </div>
  ),
};
