/**
 * Recently confirmed releases, newest first.
 */
import type { CompletedRelease } from "@/api/types";
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";
import { Identifier } from "@/components/data/identifier";
import { formatTimestamp } from "@/lib/utils";

const COLUMNS: Array<DataColumn<CompletedRelease>> = [
  {
    key: "release",
    header: "Release",
    width: "w-55",
    cell: (row) => (
      <span className="flex items-center gap-1.5 font-medium">
        <Identifier value={row.id} head={8} />
        <span className="truncate text-muted-foreground">· {row.dataset}</span>
      </span>
    ),
  },
  {
    key: "provider",
    header: "Provider · strategy",
    width: "w-40",
    cell: (row) => <span className="text-muted-foreground">{row.provider_strategy}</span>,
  },
  {
    key: "ref",
    header: "Reference",
    cell: (row) => <Identifier value={row.reference} head={14} className="text-muted-foreground" />,
  },
  {
    key: "time",
    header: "Finished",
    width: "w-28",
    cell: (row) => <span className="text-muted-foreground">{formatTimestamp(row.finished_at)}</span>,
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
  return (
    <DataTable
      columns={COLUMNS}
      rows={rows}
      rowKey={(row) => row.id}
      empty="No completed releases recorded"
      className={className}
    />
  );
}
