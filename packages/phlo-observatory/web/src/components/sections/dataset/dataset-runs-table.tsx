/**
 * Recent dataset runs, linking through to the run detail view.
 */
import type { DatasetRunRef } from "@/api/types";
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";
import { Identifier } from "@/components/data/identifier";
import { statusTone } from "@/lib/status";
import { formatTimestamp } from "@/lib/utils";

const COLUMNS: Array<DataColumn<DatasetRunRef>> = [
  {
    key: "run",
    header: "Run",
    width: "w-30",
    cell: (row) => <Identifier value={row.run_id} head={10} className="text-accent-foreground" />,
  },
  {
    key: "finished",
    header: "Finished (UTC)",
    width: "w-37.5",
    cell: (row) => <span className="text-muted-foreground">{formatTimestamp(row.finished_at)}</span>,
  },
  {
    key: "outcome",
    header: "Outcome",
    cell: (row) => <OutcomeText outcome={row.outcome} />,
  },
  { key: "release", header: "Release", width: "w-37.5", cell: (row) => row.release },
  {
    key: "duration",
    header: "Duration",
    width: "w-17.5",
    align: "right",
    cell: (row) => row.duration,
  },
];

function OutcomeText({ outcome }: { outcome: string }) {
  const tone = statusTone(outcome);
  const className =
    tone === "danger"
      ? "text-destructive"
      : tone === "success"
        ? "text-success"
        : tone === "warning"
          ? "text-warning"
          : "text-foreground";
  return <span className={className}>{outcome}</span>;
}

export function DatasetRunsTable({
  rows,
  onOpenRun,
  className,
}: {
  rows: Array<DatasetRunRef>;
  onOpenRun?: (row: DatasetRunRef) => void;
  className?: string;
}) {
  return (
    <DataTable
      columns={COLUMNS}
      rows={rows}
      rowKey={(row) => row.run_id}
      onRowClick={onOpenRun}
      className={className}
    />
  );
}
