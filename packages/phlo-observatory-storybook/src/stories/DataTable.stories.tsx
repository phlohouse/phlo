/**
 * DataTable stories: populated and empty states.
 */
import type { Meta, StoryObj } from "@storybook/react";
import { DataTable } from "../../../phlo-observatory/web/src/components/DataTable";
import { samples } from "../../../phlo-observatory/web/src/data/demo";

const meta: Meta = { title: "UI/DataTable" };
export default meta;

export const Samples: StoryObj = {
  render: () => (
    <DataTable
      head={["ID", "Name", "Status"]}
      rows={samples.slice(0, 4).map((s) => [s.id, s.name, s.status])}
      footer="Showing 4 of 8"
    />
  ),
};
export const Empty: StoryObj = { render: () => <DataTable head={["A", "B"]} rows={[]} footer="No rows" /> };
