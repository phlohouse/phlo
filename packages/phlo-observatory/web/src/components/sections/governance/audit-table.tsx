/**
 * Immutable record of governance-relevant actions.
 */
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";

export interface AuditEvent {
  time: string;
  actor: string;
  action: string;
  target: string;
  outcome: string;
  tone: string;
}

const COLUMNS: Array<DataColumn<AuditEvent>> = [
  {
    key: "time",
    header: "Time (UTC)",
    width: "w-30",
    cell: (row) => <span className="text-muted-foreground">{row.time}</span>,
  },
  { key: "actor", header: "Actor", width: "w-47.5", cell: (row) => row.actor },
  { key: "action", header: "Action", cell: (row) => row.action },
  { key: "target", header: "Target", width: "w-57.5", cell: (row) => row.target },
  {
    key: "outcome",
    header: "Outcome",
    width: "w-42.5",
    align: "right",
    cell: (row) => <span className={row.tone}>{row.outcome}</span>,
  },
];

export function AuditTable({ rows, className }: { rows: Array<AuditEvent>; className?: string }) {
  return (
    <DataTable
      columns={COLUMNS}
      rows={rows}
      rowKey={(row) => `${row.time}-${row.action}`}
      className={className}
    />
  );
}
