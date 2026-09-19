/**
 * Run detail section: run rail.
 */
import { ChevronRight } from "lucide-react";

import type {
  MissionRunDetail,
  MissionRunLogLine,
  RunArtifact,
  RunConfigurationRow,
  RunConsumer,
  RunSpan,
} from "@/api/types";
import { PropertyList } from "@/components/data/property-list";
import { SectionHeader } from "@/components/layout/section-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";

/** Right-hand rail for a run: configuration, recorded consumers and artifacts. */
export function RunDetailsRail({
  run,
  consumers,
  artifacts,
}: {
  run: MissionRunDetail;
  consumers: Array<RunConsumer>;
  artifacts: Array<RunArtifact>;
}) {
  return (
    <aside className="flex w-75 shrink-0 flex-col gap-4.5 border-l border-border pl-4.5">
      <section className="flex flex-col gap-2.75">
        <h2 className="font-display text-[15px] leading-4.5 font-semibold">Run details</h2>
        <PropertyList rows={run.details} divided={false} />
      </section>
      <section className="flex flex-col gap-2.5 border-t border-border pt-4">
        <h2 className="font-display text-[15px] leading-4.5 font-semibold">Affected consumers</h2>
        {consumers.length === 0 ? (
          <p className="text-[11px] leading-4 text-muted-foreground">
            No downstream consumers recorded for this run.
          </p>
        ) : null}
        {consumers.map((consumer) => (
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
        <SectionHeader title="Evidence artifacts" meta={`${artifacts.length} recorded`} />
        {artifacts.map((artifact) => (
          <span key={artifact.name} className="text-[11px] leading-3.5 text-accent-foreground">
            {artifact.name}
          </span>
        ))}
        {artifacts.length === 0 ? (
          <span className="text-[10px] leading-4 text-muted-foreground">
            No artifacts recorded for this run.
          </span>
        ) : null}
      </section>
    </aside>
  );
}

/** Terminal log block used by the Logs tab — cursor-paginated, so a
 * reconnecting client continues from the last retained line. */
export function LogViewer({
  lines,
  total,
  hasMore,
  onLoadMore,
}: {
  lines: Array<MissionRunLogLine>;
  total?: number | null;
  hasMore?: boolean;
  onLoadMore?: () => void;
}) {
  return (
    <section>
      <SectionHeader
        title="Logs"
        meta={
          total !== null && total !== undefined
            ? `${lines.length} of ${total} lines`
            : `${lines.length} lines`
        }
      />
      <div className="overflow-clip rounded-[7px] border border-border bg-[#14121a] p-3 font-mono text-[11px] leading-[1.7] text-[#c9c4d6]">
        {lines.map((line, index) => (
          <div key={`${index}-${line.at}-${line.message}`}>
            <span className="text-[#6b6580]">{line.at}</span>{" "}
            <span className={line.level === "ERROR" ? "text-[#ff8080]" : ""}>{line.level}</span>{" "}
            {line.message}
          </div>
        ))}
      </div>
      {hasMore ? (
        <button
          type="button"
          onClick={onLoadMore}
          className="mt-2 text-[11px] text-accent-foreground hover:underline"
        >
          Load more retained lines
        </button>
      ) : null}
    </section>
  );
}

/** Span waterfall used by the Traces tab. */
export function TraceTable({ spans }: { spans: Array<RunSpan> }) {
  return (
    <section>
      <SectionHeader title="Traces" meta={`${spans.length} spans`} />
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
            {spans.map((span) => (
              <TableRow key={span.name}>
                <TableCell className="font-mono text-[11px]">{span.name}</TableCell>
                <TableCell>
                  <span
                    className={cn(
                      "block h-2 rounded-sm opacity-75",
                      span.tone === "danger"
                        ? "bg-destructive"
                        : span.tone === "success"
                          ? "bg-success"
                          : span.tone === "warning"
                            ? "bg-warning"
                            : "bg-accent",
                    )}
                    style={{ width: `${span.width_percent}%` }}
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
export function ArtifactList({ artifacts }: { artifacts: Array<RunArtifact> }) {
  return (
    <section>
      <SectionHeader
        title="Evidence artifacts"
        meta={`${artifacts.length} recorded`}
      />
      {artifacts.map((artifact) => (
        <div
          key={artifact.name}
          className="flex items-center justify-between border-t border-border py-2.5 text-xs first:border-t-0"
        >
          <span className="text-accent-foreground">{artifact.name}</span>
          <span className="text-[11px] text-muted-foreground">
            {artifact.size} · {artifact.checksum}
          </span>
        </div>
      ))}
      {artifacts.length === 0 ? (
        <p className="pt-2 text-[10px] text-muted-foreground">
          No artifacts recorded for this run.
        </p>
      ) : null}
    </section>
  );
}

/** Configuration table used by the Configuration tab. */
export function ConfigurationPanel({ rows }: { rows: Array<{ label: string; value: string }> }) {
  return (
    <section>
      <SectionHeader title="Configuration" />
      <PropertyList rows={rows} />
    </section>
  );
}
