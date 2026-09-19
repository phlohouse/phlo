/**
 * Overview section: in-flight workflow rows first, then the most recent runs.
 */
import { ChevronRight } from "lucide-react";

import type { MissionExecutionRow } from "@/api/types";
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";
import { Section } from "@/components/layout/section-header";

function columns(): Array<DataColumn<MissionExecutionRow>> {
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
  meta,
  onOpen,
}: {
  rows: Array<MissionExecutionRow>;
  meta?: string;
  onOpen?: (row: MissionExecutionRow) => void;
}) {
  return (
    <Section title="Execution" meta={meta}>
      <DataTable columns={columns()} rows={rows} rowKey={(row) => row.id} onRowClick={onOpen} />
    </Section>
  );
}
