/**
 * Recently confirmed releases, newest first.
 */
import type { CompletedRelease } from "@/api/types";
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";

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
    cell: (row) => <span className="text-muted-foreground">{row.provider_strategy}</span>,
  },
  { key: "ref", header: "Reference", cell: (row) => row.reference },
  {
    key: "time",
    header: "Finished",
    width: "w-32.5",
    cell: (row) => <span className="text-muted-foreground">{row.finished_at}</span>,
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
