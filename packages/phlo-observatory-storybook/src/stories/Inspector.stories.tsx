/**
 * Inspector stories: sample and well layouts.
 */
import type { Meta, StoryObj } from "@storybook/react";
import { InspectorPanel } from "../../../phlo-observatory/web/src/components/InspectorPanel";

const meta: Meta = { title: "UI/Inspector" };
export default meta;

export const Sample: StoryObj = {
  render: () => (
    <InspectorPanel
      title="H358-01"
      status="Available"
      rows={[["Lot", "CELL-412"], ["Location", "FZR-04 / Box A5"], ["Volume", "1.2 mL"]]}
    />
  ),
};
export const Well: StoryObj = {
  render: () => (
    <InspectorPanel
      title="Well A1"
      rows={[["Dose", "10 µM"], ["Ct", "21.4"], ["Call", "Pass"]]}
    />
  ),
};
