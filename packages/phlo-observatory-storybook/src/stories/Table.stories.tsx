/**
 * Table stories: Paper's violet header band over hairline rows.
 */
import type { Meta, StoryObj } from "@storybook/react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const meta: Meta = { title: "UI/Table" };
export default meta;

const ROWS = [
  { wf: "orders_incremental", stage: "Ingest", progress: "1.2m rows staged", elapsed: "04:12" },
  { wf: "sales_marts", stage: "Transform", progress: "18 / 24 models complete", elapsed: "02:48" },
  { wf: "inventory_stream", stage: "Ingest", progress: "558 events · checkpoint open", elapsed: "00:36" },
];

export const ActiveExecution: StoryObj = {
  render: () => (
    <div style={{ width: 720 }} className="overflow-clip rounded-[7px] border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Workflow</TableHead>
            <TableHead>Stage</TableHead>
            <TableHead>Progress</TableHead>
            <TableHead>Elapsed</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {ROWS.map((row) => (
            <TableRow key={row.wf} clickable>
              <TableCell className="font-medium">{row.wf}</TableCell>
              <TableCell className="text-accent-foreground">{row.stage}</TableCell>
              <TableCell className="text-muted-foreground">{row.progress}</TableCell>
              <TableCell className="text-muted-foreground">{row.elapsed}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  ),
};
