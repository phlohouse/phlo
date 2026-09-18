/**
 * Queue of release candidates awaiting promotion, one row per catalog provider.
 */
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";
import { statusTone } from "@/lib/status";

export interface PendingCandidate {
  id: string;
  dataset: string;
  provider: string;
  readiness: string;
  evidence: string;
  created: string;
  action: string;
  selected?: boolean;
}

function readinessClass(readiness: string) {
  const tone = statusTone(readiness);
  return tone === "danger" ? "text-destructive" : tone === "success" ? "text-success" : "text-foreground";
}

const COLUMNS: Array<DataColumn<PendingCandidate>> = [
  {
    key: "candidate",
    header: "Candidate / dataset",
    width: "w-65",
    cell: (row) => (
      <span className="font-medium">
        {row.id} · {row.dataset}
      </span>
    ),
  },
  { key: "provider", header: "Provider · strategy", width: "w-60", cell: (row) => row.provider },
  {
    key: "readiness",
    header: "Readiness",
    width: "w-47.5",
    cell: (row) => <span className={readinessClass(row.readiness)}>{row.readiness}</span>,
  },
  { key: "evidence", header: "Evidence", cell: (row) => row.evidence },
  { key: "created", header: "Created", width: "w-16.25", cell: (row) => row.created },
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
  rows: Array<PendingCandidate>;
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
