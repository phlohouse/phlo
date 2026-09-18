/**
 * Queue of release candidates awaiting promotion, one row per catalog provider.
 */
import type { ReleaseCandidate } from "@/api/types";
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";
import { statusTone } from "@/lib/status";

function readinessClass(readiness: string) {
  const tone = statusTone(readiness);
  return tone === "danger" ? "text-destructive" : tone === "success" ? "text-success" : "text-foreground";
}

const COLUMNS: Array<DataColumn<ReleaseCandidate>> = [
  {
    key: "candidate",
    header: "Candidate / dataset",
    width: "w-65",
    cell: (row) => (
      <span className="font-medium">
        {row.dataset === row.id ? row.id : `${row.id} · ${row.dataset}`}
      </span>
    ),
  },
  { key: "provider", header: "Provider · strategy", width: "w-60", cell: (row) => `${row.provider} · ${row.strategy}` },
  {
    key: "readiness",
    header: "Readiness",
    width: "w-47.5",
    cell: (row) => <span className={readinessClass(row.readiness)}>{row.readiness}</span>,
  },
  { key: "evidence", header: "Evidence", cell: (row) => row.evidence },
  { key: "created", header: "Created", width: "w-16.25", cell: (row) => row.created_at },
  {
    key: "action",
    header: "",
    width: "w-15",
    align: "right",
    cell: (row) => <span className="text-accent-foreground">{row.action}</span>,
  },
];

export function PendingCandidatesTable({
  rows,
  selectedId,
  className,
}: {
  rows: Array<ReleaseCandidate>;
  selectedId?: string | null;
  className?: string;
}) {
  return (
    <DataTable
      columns={COLUMNS}
      rows={rows}
      rowKey={(row) => row.id}
      selectedKey={selectedId}
      className={className}
    />
  );
}
