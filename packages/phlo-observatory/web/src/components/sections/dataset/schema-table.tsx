/**
 * Dataset schema table with the field/type/nullable/role lanes.
 */
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";

export interface SchemaField {
  field: string;
  type: string;
  nullable: string;
  role: string;
}

const COLUMNS: Array<DataColumn<SchemaField>> = [
  {
    key: "field",
    header: "Field",
    width: "w-47.5",
    cell: (row) => <span className="font-medium">{row.field}</span>,
  },
  { key: "type", header: "Type", width: "w-40", cell: (row) => row.type },
  {
    key: "nullable",
    header: "Nullable",
    width: "w-22.5",
    cell: (row) => <span className="text-muted-foreground">{row.nullable}</span>,
  },
  { key: "role", header: "Role / validation", cell: (row) => row.role },
];

export function SchemaTable({
  rows,
  className,
}: {
  rows: Array<SchemaField>;
  className?: string;
}) {
  return (
    <DataTable columns={COLUMNS} rows={rows} rowKey={(row) => row.field} className={className} />
  );
}
