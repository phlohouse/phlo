/**
 * Datasets missing an ownership or contract requirement.
 */
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";

export interface OwnershipGap {
  dataset: string;
  requirement: string;
  owner: string;
  action: string;
}

const COLUMNS: Array<DataColumn<OwnershipGap>> = [
  {
    key: "dataset",
    header: "Dataset",
    width: "w-47.5",
    cell: (row) => <span className="font-medium">{row.dataset}</span>,
  },
  { key: "requirement", header: "Missing requirement", cell: (row) => row.requirement },
  {
    key: "owner",
    header: "Owner",
    width: "w-30",
    cell: (row) => <span className="text-muted-foreground">{row.owner}</span>,
  },
  {
    key: "action",
    header: "",
    width: "w-35",
    align: "right",
    cell: (row) => (
      <span className="whitespace-nowrap text-accent-foreground">{row.action}</span>
    ),
  },
];

export function OwnershipGapsTable({
  rows,
  className,
}: {
  rows: Array<OwnershipGap>;
  className?: string;
}) {
  return (
    <DataTable
      columns={COLUMNS}
      rows={rows}
      rowKey={(row) => row.dataset}
      className={className}
    />
  );
}
