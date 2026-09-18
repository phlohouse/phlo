/**
 * Declarative data table.
 *
 * Wraps the table primitives so screens describe their columns and rows rather
 * than repeating thead/tbody markup. Column width and alignment live in the
 * column definition, which is what keeps vertical lanes consistent across
 * every table in the product.
 */
import * as React from "react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

export interface DataColumn<T> {
  /** Stable identity for the column. */
  key: string;
  header: React.ReactNode;
  /** Tailwind width class, e.g. `w-37.5`. Omit to let the column flex. */
  width?: string;
  align?: "left" | "right";
  /** Cell renderer. */
  cell: (row: T) => React.ReactNode;
  /** Extra classes applied to every cell in the column. */
  className?: string;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  selectedKey,
  empty = "Nothing to show",
  className,
}: {
  columns: Array<DataColumn<T>>;
  rows: Array<T>;
  rowKey: (row: T, index: number) => string;
  onRowClick?: (row: T) => void;
  /** Row whose key matches is marked `aria-selected` for assistive tech. */
  selectedKey?: string | null;
  empty?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("overflow-clip rounded-[7px] border border-border", className)}>
      <Table>
        <TableHeader>
          <TableRow>
            {columns.map((column) => (
              <TableHead
                key={column.key}
                className={cn(
                  column.width,
                  column.align === "right" && "text-right",
                  "align-middle",
                )}
              >
                {column.header}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={columns.length}
                className="h-16 text-center text-muted-foreground"
              >
                {empty}
              </TableCell>
            </TableRow>
          ) : (
            rows.map((row, index) => {
              const key = rowKey(row, index);
              return (
                <TableRow
                  key={key}
                  clickable={Boolean(onRowClick)}
                  selected={selectedKey === key}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                >
                  {columns.map((column) => (
                    <TableCell
                      key={column.key}
                      className={cn(column.align === "right" && "text-right", column.className)}
                    >
                      {column.cell(row)}
                    </TableCell>
                  ))}
                </TableRow>
              );
            })
          )}
        </TableBody>
      </Table>
    </div>
  );
}

/**
 * Narrow variant for compact two-column evidence tables where each row is a
 * label, an explanation and a verdict.
 */
export function DataTableFooter({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-between border-t border-border px-3 py-2 text-[10px] text-muted-foreground",
        className,
      )}
    >
      {children}
    </div>
  );
}
