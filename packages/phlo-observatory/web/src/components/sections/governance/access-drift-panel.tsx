/**
 * Access drift: declared policy versus compiled grants versus what the backend
 * actually enforces.
 */
import type { AccessDriftEvidence } from "@/api/types";
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable, DataTableFooter } from "@/components/data/data-table";
import { InlineLink, Section } from "@/components/layout/section-header";

const COLUMNS: Array<DataColumn<AccessDriftEvidence>> = [
  {
    key: "evidence",
    header: "Evidence",
    width: "w-67.5",
    cell: (row) => <span className="font-medium">{row.evidence}</span>,
  },
  { key: "permissions", header: "Effective permissions", cell: (row) => row.permissions },
  {
    key: "result",
    header: "Result",
    width: "w-36.25",
    align: "right",
    cell: (row) => (
      <span className={row.drifted ? "text-destructive" : "text-muted-foreground"}>{row.result}</span>
    ),
  },
];

export function AccessDriftPanel({
  title,
  verdict,
  subtitle,
  rows,
  note,
  action,
  onPreview,
}: {
  title: string;
  verdict: string;
  subtitle: string;
  rows: Array<AccessDriftEvidence>;
  note: string;
  action: string;
  onPreview?: () => void;
}) {
  return (
    <Section
      title={title}
      action={<span className="text-[11px] text-destructive">{verdict}</span>}
    >
      <p className="text-[11px] leading-3.5 text-muted-foreground">{subtitle}</p>
      <DataTable
        columns={COLUMNS}
        rows={rows}
        rowKey={(row) => row.evidence}
        empty="No drift evidence"
      />
      <DataTableFooter className="rounded-b-[7px] border border-t-0 border-border">
        <span>{note}</span>
        <InlineLink onClick={onPreview}>{action}</InlineLink>
      </DataTableFooter>
    </Section>
  );
}
