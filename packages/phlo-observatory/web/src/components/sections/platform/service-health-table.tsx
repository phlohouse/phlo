/**
 * Enabled services table: runtime, readiness and probe outcome per service.
 */
import type { PlatformService } from "@/api/types";
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";
import { statusTone } from "@/lib/status";

function readinessClass(readiness: string) {
  const tone = statusTone(readiness);
  return tone === "success" ? "text-success" : "text-warning";
}

const COLUMNS: Array<DataColumn<PlatformService>> = [
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
  { key: "runtime", header: "Runtime", width: "w-25", cell: (row) => row.runtime_state },
  {
    key: "readiness",
    header: "Readiness",
    width: "w-25",
    cell: (row) => <span className={readinessClass(row.readiness_state)}>{row.readiness_state}</span>,
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
  rows: Array<PlatformService>;
  className?: string;
}) {
  return (
    <DataTable columns={COLUMNS} rows={rows} rowKey={(row) => row.name} className={className} />
  );
}
