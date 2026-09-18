/**
 * Recently confirmed releases, newest first.
 */
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";

export interface CompletedRelease {
  id: string;
  dataset: string;
  provider: string;
  ref: string;
  time: string;
  outcome: string;
}

const COLUMNS: Array<DataColumn<CompletedRelease>> = [
  {
    key: "release",
    header: "Release",
    width: "w-65",
    cell: (row) => (
      <span className="font-medium">
        {row.id} · {row.dataset}
      </span>
    ),
  },
  {
    key: "provider",
    header: "Provider · strategy",
    width: "w-60",
    cell: (row) => <span className="text-muted-foreground">{row.provider}</span>,
  },
  { key: "ref", header: "Reference", cell: (row) => row.ref },
  {
    key: "time",
    header: "Finished",
    width: "w-32.5",
    cell: (row) => <span className="text-muted-foreground">{row.time}</span>,
  },
  {
    key: "outcome",
    header: "Outcome",
    width: "w-32.5",
    align: "right",
    cell: (row) => <span className="text-success">{row.outcome}</span>,
  },
];

export function CompletedReleasesTable({
  rows,
  className,
}: {
  rows: Array<CompletedRelease>;
  className?: string;
}) {
  return <DataTable columns={COLUMNS} rows={rows} rowKey={(row) => row.id} className={className} />;
}
