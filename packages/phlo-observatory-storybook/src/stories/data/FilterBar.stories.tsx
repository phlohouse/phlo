/**
 * FilterBar: registry search, filter selects and trailing actions.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { FilterBar, ToolbarActions } from "@/components/data/filter-bar";

const meta: Meta = { title: "Data/FilterBar" };
export default meta;

export const Registry: StoryObj = {
  render: () => (
    <div style={{ width: 900 }}>
      <FilterBar
        search=""
        onSearch={() => {}}
        searchPlaceholder="Identifier, title or barcode…"
        filters={[
          { label: "Status: all", value: "__all__", options: ["available", "in use", "expired"] },
          { label: "Storage: all", value: "__all__", options: ["FZR-04", "FZR-02", "LN2-1"] },
        ]}
        actions={<ToolbarActions />}
      />
    </div>
  ),
};

export const SearchOnly: StoryObj = {
  render: () => (
    <div style={{ width: 600 }}>
      <FilterBar search="" onSearch={() => {}} searchPlaceholder="Filter projects…" />
    </div>
  ),
};

export const WithActiveFilter: StoryObj = {
  render: () => (
    <div style={{ width: 900 }}>
      <FilterBar
        search="SMP-00012"
        onSearch={() => {}}
        filters={[
          { label: "Condition: all", value: "4 °C", options: ["4 °C", "−20 °C", "−80 °C"] },
        ]}
        actions={<ToolbarActions />}
      />
    </div>
  ),
};
