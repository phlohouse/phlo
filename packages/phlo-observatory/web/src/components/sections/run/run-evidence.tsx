/**
 * Run detail section: run evidence.
 */
import { InlineLink, SectionHeader } from "@/components/layout/section-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { duplicateRows, runEvents, runSpans, runStages } from "@/data/demo";
import { cn } from "@/lib/utils";

const OUTCOME_TONE: Record<string, string> = {
  Succeeded: "text-success",
  Failed: "text-destructive",
  Blocked: "text-warning",
  "Not started": "text-muted-foreground",
};

/** Stage-by-stage execution timeline with proportional result bars. */
export function ExecutionTimeline() {
  return (
    <section>
      <SectionHeader title="Execution timeline" meta="5 stages · all times UTC" />
      <div className="overflow-clip rounded-[7px] border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-36.25">Stage</TableHead>
              <TableHead className="w-16.25">Provider</TableHead>
              <TableHead className="w-24.5">Outcome</TableHead>
              <TableHead className="flex-1">Timeline</TableHead>
              <TableHead className="w-12 text-right">Duration</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runStages.map((stage) => (
              <TableRow key={stage.name} className={cn(stage.flagged && "bg-destructive-soft")}>
                <TableCell className="font-medium">{stage.name}</TableCell>
                <TableCell className="text-muted-foreground">{stage.provider}</TableCell>
                <TableCell className={OUTCOME_TONE[stage.outcome] ?? "text-foreground"}>
                  {stage.outcome}
                </TableCell>
                <TableCell>
                  {stage.width > 0 ? (
                    <span className="flex items-center gap-1">
                      <span style={{ width: `${stage.offset}%` }} />
                      <span
                        className={cn(
                          "h-2 rounded-sm",
                          stage.outcome === "Failed" ? "bg-destructive" : "bg-success",
                        )}
                        style={{ width: `${stage.width}%`, opacity: 0.7 }}
                      />
                    </span>
                  ) : (
                    <span className="text-[10px] text-muted-foreground">{stage.note}</span>
                  )}
                </TableCell>
                <TableCell className="text-right text-muted-foreground">{stage.duration}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

/** Blocking quality failure with a sample of offending rows. */
export function QualityFailure() {
  return (
    <section>
      <SectionHeader title="Quality failure" meta="1 failed · 11 passed" />
      <div className="overflow-clip rounded-[7px] border border-border">
        <div className="bg-destructive-soft px-3 py-2.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-semibold">order_id must be unique</span>
            <span className="text-[11px] leading-3.5 text-destructive">Blocking · Pandera</span>
          </div>
          <p className="mt-1 text-[11px] leading-3.5 text-muted-foreground">
            42 of 1,204,000 candidate rows share an order_id. The candidate is retained for inspection.
          </p>
        </div>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-37.5">order_id</TableHead>
              <TableHead className="flex-1">record_id</TableHead>
              <TableHead className="w-45">created_at</TableHead>
              <TableHead className="w-28 text-right">Occurrences</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {duplicateRows.map((row) => (
              <TableRow key={row.recordId}>
                <TableCell>{row.orderId}</TableCell>
                <TableCell className="text-muted-foreground">{row.recordId}</TableCell>
                <TableCell className="text-muted-foreground">{row.createdAt}</TableCell>
                <TableCell className="text-right text-destructive">{row.occurrences}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <div className="flex items-center justify-between border-t border-border px-3 py-2 text-[10px] text-muted-foreground">
          <span>Sample · 3 of 42 failed rows · Candidate snapshot 938106</span>
          <InlineLink>Inspect all failed rows</InlineLink>
        </div>
      </div>
    </section>
  );
}

/** Most recent log lines, colour-coded by level. */
export function KeyEvents() {
  return (
    <section className="flex flex-col">
      <SectionHeader
        title="Key events"
        action={<InlineLink>Open full logs</InlineLink>}
      />
      <div className="flex flex-col gap-1.75">
        {runEvents.map((event) => (
          <div key={event.time + event.message} className="flex items-center">
            <span className="w-19.5 shrink-0 text-[11px] leading-3.5 text-muted-foreground">
              {event.time}
            </span>
            <span
              className={cn(
                "w-13.25 shrink-0 text-[10px] leading-3",
                event.level === "ERROR" ? "text-destructive" : "text-muted-foreground",
              )}
            >
              {event.level}
            </span>
            <span className="flex-1 text-[11px] leading-3.5 text-muted-foreground">
              {event.message}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

/** Trace span waterfall for the run. */
export function TraceWaterfall() {
  return (
    <section>
      <SectionHeader title="Traces" meta="4 spans" />
      <div className="overflow-clip rounded-[7px] border border-border">
        {runSpans.map((span, index) => (
          <div
            key={span.name}
            className={cn(
              "flex items-center gap-2.5 px-3 py-2 text-[11px]",
              index > 0 && "border-t border-border",
            )}
          >
            <span className="w-42.5 shrink-0 font-mono text-[10.5px]">{span.name}</span>
            <span className="flex-1">
              <span
                className={cn("block h-2 rounded-sm opacity-75", span.tone)}
                style={{ width: `${span.width}%` }}
              />
            </span>
            <span className="w-20 shrink-0 text-right font-mono text-[10.5px] text-muted-foreground">
              {span.duration}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
