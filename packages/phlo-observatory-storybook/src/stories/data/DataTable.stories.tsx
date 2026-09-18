/**
 * DataTable: declarative columns with alignment, selection and empty states.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { DataTable, type DataColumn } from "@/components/data/data-table";

const meta: Meta = { title: "Data/DataTable" };
export default meta;

const rows = [
  { id: "1", workflow: "orders_incremental", stage: "Ingest", progress: "1.2m rows staged", elapsed: "04:12" },
  { id: "2", workflow: "sales_marts", stage: "Transform", progress: "18 / 24 models complete", elapsed: "02:48" },
  { id: "3", workflow: "inventory_stream", stage: "Ingest", progress: "558 events · checkpoint open", elapsed: "00:36" },
  { id: "4", workflow: "events_backfill", stage: "Backfill", progress: "42 / 60 partitions complete", elapsed: "38:05" },
];

const columns: Array<DataColumn<(typeof rows)[number]>> = [
  { key: "workflow", header: "Workflow", cell: (row) => <span className="font-medium">{row.workflow}</span> },
  { key: "stage", header: "Stage", cell: (row) => <span className="text-accent-foreground">{row.stage}</span> },
  { key: "progress", header: "Progress", cell: (row) => <span className="text-muted-foreground">{row.progress}</span> },
  { key: "elapsed", header: "Elapsed", align: "right", cell: (row) => <span className="text-muted-foreground">{row.elapsed}</span> },
];

export const Default: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 760 }}>
      <DataTable columns={columns} rows={rows} rowKey={(row) => row.id} />
    </div>
  ),
};

export const WithSelectionAndClick: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 760 }}>
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(row) => row.id}
        selectedKey="2"
        onRowClick={() => {}}
      />
    </div>
  ),
};

export const Empty: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 760 }}>
      <DataTable columns={columns} rows={[]} rowKey={(row) => row.id} empty="No active workflows" />
    </div>
  ),
};

export { rows as sampleRows };
