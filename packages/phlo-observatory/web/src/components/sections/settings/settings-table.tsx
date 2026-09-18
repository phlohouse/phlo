/**
 * Simple settings table for notification rules, members and defaults.
 */
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";

export interface SettingsRow {
  name: string;
  value: string;
  meta?: string;
  tone?: string;
}

const COLUMNS: Array<DataColumn<SettingsRow>> = [
  {
    key: "name",
    header: "",
    width: "w-64",
    cell: (row) => <span className="font-medium">{row.name}</span>,
  },
  { key: "value", header: "", cell: (row) => <span className="text-muted-foreground">{row.value}</span> },
  {
    key: "meta",
    header: "",
    width: "w-40",
    align: "right",
    cell: (row) => <span className={row.tone}>{row.meta ?? ""}</span>,
  },
];

export function SettingsTable({
  rows,
  className,
}: {
  rows: Array<SettingsRow>;
  className?: string;
}) {
  return <DataTable columns={COLUMNS} rows={rows} rowKey={(row) => row.name} className={className} />;
}
