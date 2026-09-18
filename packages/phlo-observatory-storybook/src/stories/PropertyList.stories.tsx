/**
 * PropertyList stories: inspector label/value rows, divided and undivided.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { PropertyList } from "@/components/property-list";

const meta: Meta = { title: "Domain/PropertyList" };
export default meta;

const ROWS = [
  { label: "Asset", value: "marts.orders" },
  { label: "Orchestrator", value: "Dagster" },
  { label: "Trigger", value: "Schedule · orders_hourly" },
  { label: "Partition", value: "2026-09-13" },
];

export const Divided: StoryObj = {
  render: () => (
    <div style={{ width: 300 }}>
      <PropertyList rows={ROWS} />
    </div>
  ),
};

export const Undivided: StoryObj = {
  render: () => (
    <div style={{ width: 300 }}>
      <PropertyList rows={ROWS} divided={false} />
    </div>
  ),
};
