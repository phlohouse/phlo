/**
 * Publication reviews awaiting a policy verdict.
 */
import type { PublicationReview } from "@/api/types";
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";
import { statusTone } from "@/lib/status";

function verdictClass(verdict: string) {
  const tone = statusTone(verdict);
  return tone === "success" ? "text-success" : "text-warning";
}

const COLUMNS: Array<DataColumn<PublicationReview>> = [
  {
    key: "dataset",
    header: "Dataset",
    width: "w-52.5",
    cell: (row) => <span className="font-medium">{row.dataset}</span>,
  },
  { key: "owner", header: "Owner", width: "w-37.5", cell: (row) => row.owner },
  { key: "contract", header: "Contract", width: "w-42.5", cell: (row) => row.contract },
  {
    key: "verdict",
    header: "Verdict",
    width: "w-27.5",
    cell: (row) => <span className={verdictClass(row.verdict)}>{row.verdict}</span>,
  },
  { key: "reason", header: "Reason", cell: (row) => row.reason },
  {
    key: "action",
    header: "",
    width: "w-17.5",
    align: "right",
    cell: (row) => <span className="text-accent-foreground">{row.action}</span>,
  },
];

export function PublicationReviewsTable({
  rows,
  className,
}: {
  rows: Array<PublicationReview>;
  className?: string;
}) {
  const selected = rows.find((row) => row.selected)?.dataset ?? null;
  return (
    <DataTable
      columns={COLUMNS}
      rows={rows}
      rowKey={(row) => row.dataset}
      selectedKey={selected}
      className={className}
    />
  );
}
