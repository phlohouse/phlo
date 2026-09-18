/**
 * Overview page section: execution table.
 */
import { ChevronRight } from "lucide-react";
import { useNavigate } from "@tanstack/react-router";

import type {ExecutionRow} from "@/data/demo";
import { SectionHeader } from "@/components/section-header";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {  activeExecution } from "@/data/demo";

const COLUMNS = ["Workflow", "Stage", "Progress", "Elapsed"] as const;

/** "Active execution" — running and queued workflow rows. */
export function ExecutionTable() {
  const navigate = useNavigate();
  return (
    <section>
      <SectionHeader title="Active execution" meta="4 running · 2 queued" />
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
            {activeExecution.map((row: ExecutionRow) => (
              <TableRow key={row.workflow} clickable onClick={() => navigate({ to: row.to })}>
                <TableCell className="font-medium">{row.workflow}</TableCell>
                <TableCell className="text-accent-foreground">{row.stage}</TableCell>
                <TableCell className="text-muted-foreground">{row.progress}</TableCell>
                <TableCell className="text-muted-foreground">{row.elapsed}</TableCell>
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
