/**
 * Overview section: running and queued workflow rows.
 */
import { ChevronRight } from "lucide-react";

import type { ExecutionRow } from "@/data/demo";
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";
import { Section } from "@/components/layout/section-header";

function columns(): Array<DataColumn<ExecutionRow>> {
  return [
    {
      key: "workflow",
      header: "Workflow",
      cell: (row) => <span className="font-medium">{row.workflow}</span>,
    },
    {
      key: "stage",
      header: "Stage",
      cell: (row) => <span className="text-accent-foreground">{row.stage}</span>,
    },
    {
      key: "progress",
      header: "Progress",
      cell: (row) => <span className="text-muted-foreground">{row.progress}</span>,
    },
    {
      key: "elapsed",
      header: "Elapsed",
      cell: (row) => <span className="text-muted-foreground">{row.elapsed}</span>,
    },
    {
      key: "chevron",
      header: "",
      width: "w-4.5",
      cell: () => <ChevronRight className="size-4.5 text-muted-foreground" />,
    },
  ];
}

export function ExecutionTable({
  rows,
  meta = "4 running · 2 queued",
  onOpen,
}: {
  rows: Array<ExecutionRow>;
  meta?: string;
  onOpen?: (row: ExecutionRow) => void;
}) {
  return (
    <Section title="Active execution" meta={meta}>
      <DataTable columns={columns()} rows={rows} rowKey={(row) => row.workflow} onRowClick={onOpen} />
    </Section>
  );
}
