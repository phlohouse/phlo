/**
 * PropertyList and EvidenceList: the inspector and evidence row patterns.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { EvidenceList, EvidenceListPlain } from "@/components/data/evidence-list";
import { PropertyList } from "@/components/data/property-list";

const meta: Meta = { title: "Data/PropertyList" };
export default meta;

const PROPERTIES = [
  { label: "Asset", value: "marts.orders" },
  { label: "Orchestrator", value: "Dagster" },
  { label: "Trigger", value: "Schedule · orders_hourly" },
  { label: "Partition", value: "2026-09-13" },
  { label: "Candidate snapshot", value: "938106" },
];

const EVIDENCE = [
  { name: "Quality checks", detail: "14 passed · 0 blocking failures", outcome: "Passed", tone: "text-success" },
  { name: "Run evidence", detail: "All required stages and artifacts recorded", outcome: "Complete", tone: "text-success" },
  { name: "Snapshot audit", detail: "Both candidate snapshots match audit", outcome: "Matched", tone: "text-success" },
  { name: "Release revision", detail: "Expected 42 · Observed 42", outcome: "Current", tone: "text-success" },
];

export const Divided: StoryObj = {
  render: () => (
    <div style={{ width: 320 }}>
      <PropertyList rows={PROPERTIES} />
    </div>
  ),
};

export const Undivided: StoryObj = {
  render: () => (
    <div style={{ width: 320 }}>
      <PropertyList rows={PROPERTIES} divided={false} />
    </div>
  ),
};

export const EvidenceRows: StoryObj = {
  render: () => (
    <div style={{ width: 620 }}>
      <EvidenceList rows={EVIDENCE} />
    </div>
  ),
};

export const EvidenceRowsWithFailures: StoryObj = {
  render: () => (
    <div style={{ width: 620 }}>
      <EvidenceList
        rows={[
          { name: "Quality checks", detail: "11 passed · 1 blocking failure", outcome: "Failed", tone: "text-destructive" },
          { name: "Run evidence", detail: "All required stages recorded", outcome: "Complete", tone: "text-success" },
          { name: "Snapshot audit", detail: "Not evaluated", outcome: "Unknown", tone: "text-muted-foreground" },
        ]}
      />
    </div>
  ),
};

export const PlainRows: StoryObj = {
  render: () => (
    <div style={{ width: 320 }}>
      <EvidenceListPlain
        rows={[
          { label: "Alloy → Loki", value: "Delivery unconfirmed", tone: "text-warning" },
          { label: "Loki → Log queries", value: "Unavailable", tone: "text-destructive" },
          { label: "Postgres → Run evidence", value: "Available", tone: "text-success" },
        ]}
      />
    </div>
  ),
};
