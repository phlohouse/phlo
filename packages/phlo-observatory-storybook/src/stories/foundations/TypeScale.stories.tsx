/**
 * Type scale: the seven steps the product uses, largest to smallest.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { TypeScaleTable } from "@/components/foundation/type-scale";

const meta: Meta = { title: "Foundations/Type scale" };
export default meta;

const SAMPLES = [
  {
    token: "display — 25px / 600",
    label: "Overview",
    className: "font-display text-[25px] leading-7.25 font-semibold tracking-[-0.03em]",
  },
  {
    token: "metric — 17px / 600",
    label: "128 datasets",
    className: "font-display text-[17px] leading-5.5 font-semibold tracking-[-0.02em]",
  },
  {
    token: "section — 15px / 600",
    label: "Needs attention",
    className: "font-display text-[15px] leading-4.5 font-semibold",
  },
  {
    token: "title — 13px / 600",
    label: "Orders delivery blocked",
    className: "text-[13px] leading-4 font-semibold",
  },
  { token: "body — 12px / 400", label: "Order-level revenue and fulfilment data", className: "text-xs leading-4" },
  { token: "label — 11px / 500", label: "Provider · strategy", className: "text-[11px] leading-3.5 font-medium" },
  { token: "caption — 10px / 400", label: "121 fresh · 4 late · 3 unknown", className: "text-[10px] leading-3" },
];

export const Scale: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 720 }}>
      <TypeScaleTable samples={SAMPLES} />
    </div>
  ),
};
