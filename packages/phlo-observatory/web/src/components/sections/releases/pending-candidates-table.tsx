/**
 * Queue of release candidates awaiting promotion, one row per catalog provider.
 */
import type { ReleaseCandidate } from "@/api/types";
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";
import { Identifier } from "@/components/data/identifier";
import { statusTone } from "@/lib/status";
import { formatTimestamp } from "@/lib/utils";

function readinessClass(readiness: string) {
  const tone = statusTone(readiness);
  return tone === "danger" ? "text-destructive" : tone === "success" ? "text-success" : "text-foreground";
}

const COLUMNS: Array<DataColumn<ReleaseCandidate>> = [
  {
    key: "candidate",
    header: "Candidate / dataset",
    width: "w-55",
    cell: (row) => (
      <span className="flex items-center gap-1.5 font-medium">
        <Identifier value={row.id} head={8} />
        {row.dataset === row.id ? null : (
          <span className="truncate text-muted-foreground">· {row.dataset}</span>
        )}
      </span>
    ),
  },
  { key: "provider", header: "Provider · strategy", width: "w-46", cell: (row) => `${row.provider} · ${row.strategy}` },
  {
    key: "readiness",
    header: "Readiness",
    width: "w-28",
    cell: (row) => <span className={readinessClass(row.readiness)}>{row.readiness}</span>,
  },
  {
    key: "evidence",
    header: "Evidence",
    cell: (row) => {
      const blocker = row.blockers?.[0];
      const text = blocker ?? row.evidence;
      return (
        <span className="block truncate text-muted-foreground" title={text}>
          {text}
        </span>
      );
    },
  },
  { key: "created", header: "Created", width: "w-28", cell: (row) => formatTimestamp(row.created_at) },
];

export function PendingCandidatesTable({
  rows,
  selectedId,
  onSelect,
  className,
}: {
  rows: Array<ReleaseCandidate>;
  selectedId?: string | null;
  onSelect?: (row: ReleaseCandidate) => void;
  className?: string;
}) {
  return (
    <DataTable
      columns={COLUMNS}
      rows={rows}
      rowKey={(row) => row.id}
      selectedKey={selectedId}
      onRowClick={onSelect}
      empty="No release candidates recorded"
      className={className}
    />
  );
}
