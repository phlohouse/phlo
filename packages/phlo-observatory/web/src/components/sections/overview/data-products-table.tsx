/**
 * Overview section: released datasets with freshness and quality signals.
 */
import { ChevronRight } from "lucide-react";

import type { DataProductRow } from "@/data/demo";
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";
import { InlineLink, Section } from "@/components/layout/section-header";

function freshnessClass(value: string) {
  return value === "Fresh" ? "text-success" : "text-warning";
}

function qualityClass(value: string) {
  if (value === "Passed") return "text-success";
  if (value === "Unknown") return "text-muted-foreground";
  if (/fail/i.test(value)) return "text-destructive";
  return "text-warning";
}

function columns(): Array<DataColumn<DataProductRow>> {
  return [
    {
      key: "name",
      header: "Dataset",
      cell: (row) => <span className="font-semibold">{row.name}</span>,
    },
    {
      key: "freshness",
      header: "Freshness",
      cell: (row) => <span className={freshnessClass(row.freshness)}>{row.freshness}</span>,
    },
    {
      key: "quality",
      header: "Quality",
      cell: (row) => <span className={qualityClass(row.quality)}>{row.quality}</span>,
    },
    {
      key: "released",
      header: "Last released",
      cell: (row) => <span className="text-muted-foreground">{row.released}</span>,
    },
    {
      key: "consumers",
      header: "Consumers",
      cell: (row) => <span className="text-muted-foreground">{row.consumers}</span>,
    },
    {
      key: "chevron",
      header: "",
      width: "w-4.5",
      cell: () => <ChevronRight className="size-4.5 text-muted-foreground" />,
    },
  ];
}

export function DataProductsTable({
  rows,
  total = 128,
  onOpen,
  onViewAll,
}: {
  rows: Array<DataProductRow>;
  total?: number;
  onOpen?: (row: DataProductRow) => void;
  onViewAll?: () => void;
}) {
  return (
    <Section
      title="Data products"
      action={
        <span className="flex items-center gap-1.5 text-[11px] leading-3.5 text-muted-foreground">
          Showing {rows.length} of {total} ·
          <InlineLink onClick={onViewAll}>View all datasets</InlineLink>
        </span>
      }
    >
      <DataTable columns={columns()} rows={rows} rowKey={(row) => row.name} onRowClick={onOpen} />
    </Section>
  );
}
