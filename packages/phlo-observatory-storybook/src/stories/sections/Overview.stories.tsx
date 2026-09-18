/**
 * Overview sections: attention list, execution table, data products and rail.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { AttentionList } from "@/components/sections/overview/attention-list";
import { DataProductsTable } from "@/components/sections/overview/data-products-table";
import { ExecutionTable } from "@/components/sections/overview/execution-table";
import { HealthRail } from "@/components/sections/overview/health-rail";
import {
  activeExecution,
  attentionItems,
  dataProducts,
  governanceOverview,
  recoveryOverview,
  releaseQueue,
  services,
} from "@/data/demo";

const meta: Meta = { title: "Sections/Overview" };
export default meta;

export const NeedsAttention: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 820 }}>
      <AttentionList items={attentionItems} />
    </div>
  ),
};

export const ActiveExecution: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 820 }}>
      <ExecutionTable rows={activeExecution} />
    </div>
  ),
};

export const DataProducts: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 820 }}>
      <DataProductsTable rows={dataProducts} />
    </div>
  ),
};

export const Rail: StoryObj = {
  render: () => (
    <HealthRail
      services={services.slice(0, 6)}
      totalServices={services.length}
      releaseQueue={releaseQueue}
      governance={governanceOverview}
      recovery={recoveryOverview}
      readyCount={services.filter((service) => service.state === "Ready").length}
      totalReady={services.length}
    />
  ),
};
