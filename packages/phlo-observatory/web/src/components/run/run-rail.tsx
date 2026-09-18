/**
 * Run detail section: run rail.
 */
import { ChevronRight } from "lucide-react";

import { PropertyList } from "@/components/property-list";
import { SectionHeader } from "@/components/section-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { runArtifacts, runConsumers, runDetails, runLogLines, runSpans } from "@/data/demo";
import { cn } from "@/lib/utils";

/** Right-hand rail for a run: configuration, blast radius and artifacts. */
export function RunDetailsRail() {
  return (
    <aside className="flex w-75 shrink-0 flex-col gap-4.5 border-l border-border pl-4.5">
      <section className="flex flex-col gap-2.75">
        <h2 className="font-display text-[15px] leading-4.5 font-semibold">Run details</h2>
        <PropertyList rows={runDetails} divided={false} />
      </section>
      <section className="flex flex-col gap-2.5 border-t border-border pt-4">
        <h2 className="font-display text-[15px] leading-4.5 font-semibold">Affected consumers</h2>
        <p className="text-[11px] leading-4 text-muted-foreground">
          All three still read the last successful delivery at 08:00.
        </p>
        {runConsumers.map((consumer) => (
          <div key={consumer.name} className="flex items-center gap-2">
            <span className="flex flex-1 flex-col gap-0.75">
              <span className="text-xs font-medium">{consumer.name}</span>
              <span className="text-[10px] leading-3 text-muted-foreground">{consumer.role}</span>
            </span>
            <ChevronRight className="size-4.5 text-muted-foreground" />
          </div>
        ))}
      </section>
      <section className="flex flex-col gap-2.5 border-t border-border pt-4">
        <SectionHeader title="Evidence artifacts" meta="6 files" />
        {runArtifacts.map((artifact) => (
          <span key={artifact} className="text-[11px] leading-3.5 text-accent-foreground">
            {artifact}
          </span>
        ))}
        <span className="text-[10px] leading-4 text-muted-foreground">
          Checksums recorded · Retained for 30 days
        </span>
      </section>
    </aside>
  );
}

/** Terminal log block used by the Logs tab. */
export function LogViewer() {
  return (
    <section>
      <SectionHeader title="Logs" meta={`${runLogLines.length} of 128 lines`} />
      <div className="overflow-clip rounded-[7px] border border-border bg-[#14121a] p-3 font-mono text-[11px] leading-[1.7] text-[#c9c4d6]">
        {runLogLines.map((line) => (
          <div key={line.time + line.message}>
            <span className="text-[#6b6580]">{line.time}</span>{" "}
            <span className={line.level === "ERROR" ? "text-[#ff8080]" : ""}>{line.level}</span>{" "}
            {line.message}
          </div>
        ))}
      </div>
    </section>
  );
}

/** Span waterfall used by the Traces tab. */
export function TraceTable() {
  return (
    <section>
      <SectionHeader title="Traces" meta={`${runSpans.length} spans`} />
      <div className="overflow-clip rounded-[7px] border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-42.5">Span</TableHead>
              <TableHead />
              <TableHead className="w-24 text-right">Duration</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runSpans.map((span) => (
              <TableRow key={span.name}>
                <TableCell className="font-mono text-[11px]">{span.name}</TableCell>
                <TableCell>
                  <span
                    className={cn("block h-2 rounded-sm opacity-75", span.tone)}
                    style={{ width: `${span.width}%` }}
                  />
                </TableCell>
                <TableCell className="text-right font-mono text-[11px] text-muted-foreground">
                  {span.duration}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

/** Evidence artifact list used by the Artifacts tab. */
export function ArtifactList() {
  return (
    <section>
      <SectionHeader title="Evidence artifacts" meta="6 files" />
      {runArtifacts.map((artifact) => (
        <div key={artifact} className="flex items-center justify-between border-t border-border py-2.5 text-xs first:border-t-0">
          <span className="text-accent-foreground">{artifact}</span>
          <span className="text-[11px] text-muted-foreground">checksum recorded</span>
        </div>
      ))}
      <p className="pt-2 text-[10px] text-muted-foreground">
        Checksums recorded · Retained for 30 days
      </p>
    </section>
  );
}

/** Configuration table used by the Configuration tab. */
export function ConfigurationPanel({ rows }: { rows: { label: string; value: string }[] }) {
  return (
    <section>
      <SectionHeader title="Configuration" />
      <PropertyList rows={rows} />
    </section>
  );
}
