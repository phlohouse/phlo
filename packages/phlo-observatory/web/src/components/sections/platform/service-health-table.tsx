/**
 * Enabled services table: runtime, readiness and probe outcome per service.
 */
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";
import { statusTone } from "@/lib/status";

export interface PlatformServiceRow {
  name: string;
  role: string;
  runtime: string;
  readiness: string;
  probe: string;
  action: string;
  /** Rows needing attention are tinted on the warning surface. */
  attention?: boolean;
}

function readinessClass(readiness: string) {
  const tone = statusTone(readiness);
  return tone === "success" ? "text-success" : "text-warning";
}

const COLUMNS: Array<DataColumn<PlatformServiceRow>> = [
  {
    key: "name",
    header: "Service",
    width: "w-35",
    cell: (row) => <span className="font-medium">{row.name}</span>,
  },
  {
    key: "role",
    header: "Role",
    cell: (row) => <span className="text-muted-foreground">{row.role}</span>,
  },
  { key: "runtime", header: "Runtime", width: "w-25", cell: (row) => row.runtime },
  {
    key: "readiness",
    header: "Readiness",
    width: "w-25",
    cell: (row) => <span className={readinessClass(row.readiness)}>{row.readiness}</span>,
  },
  {
    key: "probe",
    header: "Probe",
    width: "w-28.75",
    cell: (row) => <span className="text-muted-foreground">{row.probe}</span>,
  },
  {
    key: "action",
    header: "",
    width: "w-13.75",
    align: "right",
    cell: (row) => <span className="text-accent-foreground">{row.action}</span>,
  },
];

export function ServiceHealthTable({
  rows,
  className,
}: {
  rows: Array<PlatformServiceRow>;
  className?: string;
}) {
  return (
    <DataTable columns={COLUMNS} rows={rows} rowKey={(row) => row.name} className={className} />
  );
}
