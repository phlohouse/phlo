/**
 * PageHeader, SectionHeader, Section and SectionCard: the page rhythm.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { PageHeader, ReadStateChip } from "@/components/layout/page-header";
import { Section, SectionHeader, InlineLink } from "@/components/layout/section-header";
import { SectionCard } from "@/components/layout/section-card";
import { StatusPill } from "@/components/data/status-pill";
import { Button } from "@/components/ui/button";

const meta: Meta = { title: "Layout/PageHeader" };
export default meta;

const demoEvidence = {
  status: "demo" as const,
  project_id: "storybook",
  environment_id: "storybook",
  source: "fixture",
  observed_at: new Date().toISOString(),
  last_confirmed_at: null,
  reason_code: null,
  detail: null,
  dropped_records: 0,
};

export const Overview: StoryObj = {
  render: () => (
    <div style={{ width: 1000 }}>
      <PageHeader
        title="Overview"
        titleAccessory={<ReadStateChip evidence={demoEvidence} />}
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

export const WithStatusAndDescription: StoryObj = {
  render: () => (
    <div style={{ width: 1000 }}>
      <PageHeader
        title="orders_daily"
        titleAccessory={
          <>
            <StatusPill tone="danger" dot={false}>
              Failed validation
            </StatusPill>
            <ReadStateChip evidence={demoEvidence} />
          </>
        }
        description="Run r7e42b · 13 Sep 2026, 09:25 UTC · Scheduled · Partition 2026-09-13"
        actions={<Button>Preview retry</Button>}
      />
    </div>
  ),
};

export const HeadingsAndBlocks: StoryObj = {
  render: () => (
    <div style={{ display: "flex", flexDirection: "column", gap: 24, width: 620 }}>
      <SectionHeader title="Needs attention" meta="3 priority items · ranked by consumer impact" />
      <Section title="Schema" action={<InlineLink>8 columns · View schema →</InlineLink>}>
        <div className="rounded-[7px] border border-border p-3 text-xs text-muted-foreground">
          Section content sits in a 7px block with a 10px heading rhythm.
        </div>
      </Section>
      <SectionCard
        title="Connections · 3"
        meta="Checks run every 60s · outcomes recorded in Audit"
      >
        <div className="p-3 text-xs text-muted-foreground">
          SectionCard adds the violet header band and body padding.
        </div>
      </SectionCard>
    </div>
  ),
};
