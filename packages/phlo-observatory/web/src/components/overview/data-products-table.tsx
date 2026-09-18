/**
 * Overview page section: data products table.
 */
import { ChevronRight } from "lucide-react";
import { useNavigate } from "@tanstack/react-router";

import { InlineLink, SectionHeader } from "@/components/section-header";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { dataProducts } from "@/data/demo";
import { cn } from "@/lib/utils";

const COLUMNS = ["Dataset", "Freshness", "Quality", "Last released", "Consumers"] as const;

function freshnessClass(value: string) {
  return value === "Fresh" ? "text-success" : "text-warning";
}

function qualityClass(value: string) {
  if (value === "Passed") return "text-success";
  if (value === "Unknown") return "text-muted-foreground";
  if (/fail/i.test(value)) return "text-destructive";
  return "text-warning";
}

/** "Data products" — released datasets with freshness and quality signals. */
export function DataProductsTable() {
  const navigate = useNavigate();
  return (
    <section>
      <SectionHeader
        title="Data products"
        action={
          <span className="flex items-center gap-1.5 text-[11px] leading-3.5 text-muted-foreground">
            Showing 5 of 128 ·
            <InlineLink onClick={() => navigate({ to: "/datasets/orders" })}>
              View all datasets
            </InlineLink>
          </span>
        }
      />
      <div className="overflow-clip rounded-[7px] border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              {COLUMNS.map((column) => (
                <TableHead key={column}>{column}</TableHead>
              ))}
              <TableHead className="w-4.5" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {dataProducts.map((row) => (
              <TableRow key={row.name} clickable onClick={() => navigate({ to: row.to })}>
                <TableCell className="font-semibold">{row.name}</TableCell>
                <TableCell className={cn(freshnessClass(row.freshness))}>{row.freshness}</TableCell>
                <TableCell className={cn(qualityClass(row.quality))}>{row.quality}</TableCell>
                <TableCell className="text-muted-foreground">{row.released}</TableCell>
                <TableCell className="text-muted-foreground">{row.consumers}</TableCell>
                <TableCell>
                  <ChevronRight className="size-4.5 text-muted-foreground" />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}
