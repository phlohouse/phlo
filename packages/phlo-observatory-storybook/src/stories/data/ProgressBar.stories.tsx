/**
 * ProgressBar, OffsetBar and MeterBar: proportional result bars.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { MeterBar, OffsetBar, ProgressBar } from "@/components/data/progress-bar";

const meta: Meta = { title: "Data/ProgressBar" };
export default meta;

export const Tones: StoryObj = {
  render: () => (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, width: 420 }}>
      <ProgressBar value={72} tone="success" />
      <ProgressBar value={44} tone="primary" />
      <ProgressBar value={28} tone="warning" />
      <ProgressBar value={12} tone="danger" />
      <ProgressBar value={56} tone="muted" />
    </div>
  ),
};

export const TimelineOffsets: StoryObj = {
  render: () => (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, width: 520 }}>
      <OffsetBar offset={0} width={25} tone="success" />
      <OffsetBar offset={25} width={54} tone="success" />
      <OffsetBar offset={79} width={21} tone="danger" />
      <OffsetBar offset={0} width={0} label="No provider mutation" />
    </div>
  ),
};

export const Meters: StoryObj = {
  render: () => (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, width: 300 }}>
      <MeterBar value={96} tone="primary" />
      <MeterBar value={62} tone="primary" />
      <MeterBar value={18} tone="warning" />
    </div>
  ),
};
