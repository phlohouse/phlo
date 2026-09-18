/**
 * MetricStrip: the summary band, with and without cell dividers, plus tone
 * overrides for values that carry a status.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { MetricStrip } from "@/components/data/metric-strip";

const meta: Meta = { title: "Data/MetricStrip" };
export default meta;

const METRICS = [
  { label: "Data", value: "128 datasets", hint: "121 fresh · 4 late · 3 unknown" },
  { label: "Ingestion", value: "18 sources", hint: "16 current · 1 delayed · 1 idle" },
  { label: "Quality", value: "612 checks", hint: "608 passed · 3 warnings · 1 failed" },
  { label: "Execution", value: "156 runs", hint: "149 succeeded · 4 active · 3 failed" },
  { label: "Releases", value: "2 pending", hint: "1 ready · 1 evidence blocked" },
  { label: "Governance", value: "124 owned", hint: "4 unassigned · 2 reviews due" },
];

const TONED = [
  { label: "Enabled services", value: "12 of 16", hint: "4 discovered, not enabled" },
  { label: "Running", value: "12 of 12", hint: "Container state observed" },
  { label: "Ready", value: "11 of 12", hint: "1 readiness probe failing", tone: "text-warning" },
  { label: "Telemetry", value: "Partial", hint: "Loki log queries unavailable", tone: "text-warning" },
  { label: "Restore rehearsal", value: "Verified", hint: "11 Sep · Isolated target", tone: "text-success" },
];

export const WithDividers: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 1100 }}>
      <MetricStrip metrics={METRICS} />
    </div>
  ),
};

export const WithoutDividers: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 1100 }}>
      <MetricStrip metrics={METRICS} dividers={false} />
    </div>
  ),
};

export const WithTones: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 1100 }}>
      <MetricStrip metrics={TONED} dividers={false} />
    </div>
  ),
};
