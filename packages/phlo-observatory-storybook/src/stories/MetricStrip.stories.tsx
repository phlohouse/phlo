/**
 * MetricStrip stories: the summary band with and without cell dividers.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { MetricStrip } from "@/components/metric-strip";

const meta: Meta = { title: "Domain/MetricStrip" };
export default meta;

const METRICS = [
  { label: "Data", value: "128 datasets", hint: "121 fresh · 4 late · 3 unknown" },
  { label: "Ingestion", value: "18 sources", hint: "16 current · 1 delayed · 1 idle" },
  { label: "Quality", value: "612 checks", hint: "608 passed · 3 warnings · 1 failed" },
  { label: "Execution", value: "156 runs", hint: "149 succeeded · 4 active · 3 failed" },
];

export const WithDividers: StoryObj = {
  render: () => (
    <div style={{ width: 900 }}>
      <MetricStrip metrics={METRICS} />
    </div>
  ),
};

export const WithoutDividers: StoryObj = {
  render: () => (
    <div style={{ width: 900 }}>
      <MetricStrip metrics={METRICS} dividers={false} />
    </div>
  ),
};
