/**
 * DetailRail and DetailSection: the right-hand inspector column.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { DetailRail, DetailSection } from "@/components/layout/detail-rail";
import { EvidenceListPlain } from "@/components/data/evidence-list";
import { PropertyList } from "@/components/data/property-list";
import { StatusPill } from "@/components/data/status-pill";
import { InlineLink } from "@/components/layout/section-header";
import { Button } from "@/components/ui/button";

const meta: Meta = { title: "Layout/DetailRail" };
export default meta;

export const RunRail: StoryObj = {
  render: () => (
    <DetailRail>
      <DetailSection title="Run details">
        <PropertyList
          divided={false}
          rows={[
            { label: "Asset", value: "marts.orders" },
            { label: "Orchestrator", value: "Dagster" },
            { label: "Partition", value: "2026-09-13" },
            { label: "Candidate branch", value: "wap/r7e42b" },
          ]}
        />
      </DetailSection>
      <DetailSection title="Affected consumers" divided>
        <p className="text-[11px] leading-4 text-muted-foreground">
          All three still read the last successful delivery at 08:00.
        </p>
      </DetailSection>
    </DetailRail>
  ),
};

export const PublicationPlan: StoryObj = {
  render: () => (
    <DetailRail>
      <DetailSection title="Publish Shipments" action={<StatusPill status="Ready" tone="success" />}>
        <p className="text-[11px] leading-4 text-muted-foreground">
          Review the internal Dataset publication transition.
        </p>
        <EvidenceListPlain
          rows={[
            { label: "Dataset", value: "logistics.shipments" },
            { label: "Owner", value: "Logistics" },
            { label: "Classification", value: "Internal" },
            { label: "Transition", value: "Draft → Published" },
          ]}
        />
        <Button className="w-full">Preview Dataset publication</Button>
      </DetailSection>
      <DetailSection title="Consumer read contract" divided>
        <p className="text-xs leading-4.5">
          Consistent reads across both tables must resolve snapshots through the release record.
        </p>
        <InlineLink>Inspect provider guarantees</InlineLink>
      </DetailSection>
    </DetailRail>
  ),
};
