/**
 * Recent dataset runs, linking through to the run detail view.
 */
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";
import { statusTone } from "@/lib/status";

export interface DatasetRunRow {
  run: string;
  finished: string;
  outcome: string;
  release: string;
  duration: string;
}

const COLUMNS: Array<DataColumn<DatasetRunRow>> = [
  {
    key: "run",
    header: "Run",
    width: "w-37.5",
    cell: (row) => <span className="text-accent-foreground">{row.run}</span>,
  },
  { key: "finished", header: "Finished (UTC)", width: "w-37.5", cell: (row) => row.finished },
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
  rows: Array<DatasetRunRow>;
  onOpenRun?: (row: DatasetRunRow) => void;
  className?: string;
}) {
  return (
    <DataTable
      columns={COLUMNS}
      rows={rows}
      rowKey={(row) => row.run}
      onRowClick={onOpenRun}
      className={className}
    />
  );
}
