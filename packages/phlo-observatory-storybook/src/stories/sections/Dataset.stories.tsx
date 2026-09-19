/**
 * Dataset sections: lineage chain, schema table, runs table and ownership rail.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { DatasetOwnershipRail } from "@/components/sections/dataset/dataset-ownership-rail";
import { DatasetRunsTable } from "@/components/sections/dataset/dataset-runs-table";
import { LineageChain } from "@/components/sections/dataset/lineage-chain";
import { SchemaTable } from "@/components/sections/dataset/schema-table";
import { datasetAccessRows, datasetOwnershipView, datasetRunRows, datasetSchema } from "../../fixtures/demo";

const meta: Meta = { title: "Sections/Dataset" };
export default meta;

export const Lineage: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 820 }}>
      <LineageChain
        nodes={[
          { name: "postgres.orders", role: "Postgres · Source" },
          { name: "stg_orders", role: "dbt · Model" },
          { name: "marts.orders", role: "This dataset", current: true },
          { name: "3 consumers", role: "Analytics · API · dbt" },
        ]}
        caption="Declared dependencies · Consumers read released snapshot 938105"
      />
    </div>
  ),
};

export const Schema: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 820 }}>
      <SchemaTable rows={datasetSchema} />
    </div>
  ),
};

export const Runs: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 820 }}>
      <DatasetRunsTable rows={datasetRunRows} />
    </div>
  ),
};

export const OwnershipRail: StoryObj = {
  render: () => (
    <DatasetOwnershipRail
      ownership={datasetOwnershipView}
      access={datasetAccessRows}
    />
  ),
};
