/**
 * Run detail section: run evidence.
 */
import type {
  RunCheckOutcome,
  RunEvent,
  RunIdentity,
  RunQualityReport,
  RunSpan,
  RunStageView,
} from "@/api/types";
import { Identifier } from "@/components/data/identifier";
import { InlineLink, SectionHeader } from "@/components/layout/section-header";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn, formatTimestamp } from "@/lib/utils";

const OUTCOME_TONE: Record<string, string> = {
  Succeeded: "text-success",
  Failed: "text-destructive",
  Blocked: "text-warning",
  "Not started": "text-muted-foreground",
};

/** Stage-by-stage execution timeline with proportional result bars. */
export function ExecutionTimeline({ stages }: { stages: Array<RunStageView> }) {
  return (
    <section>
      <SectionHeader
        title="Execution timeline"
        meta={`${stages.length} stage${stages.length === 1 ? "" : "s"} · all times UTC`}
      />
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
            {stages.map((stage) => (
              <TableRow key={stage.name} className={cn(stage.flagged && "bg-destructive-soft")}>
                <TableCell className="font-medium">{stage.name}</TableCell>
                <TableCell className="text-muted-foreground">{stage.provider}</TableCell>
                <TableCell className={OUTCOME_TONE[stage.outcome] ?? "text-foreground"}>
                  {stage.outcome}
                </TableCell>
                <TableCell>
                  {stage.width_percent > 0 ? (
                    <span className="flex items-center gap-1">
                      <span style={{ width: `${stage.offset_percent}%` }} />
                      <span
                        className={cn(
                          "h-2 rounded-sm",
                          stage.outcome === "Failed" ? "bg-destructive" : "bg-success",
                        )}
                        style={{ width: `${stage.width_percent}%`, opacity: 0.7 }}
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

const CHECK_TONE: Record<string, string> = {
  success: "text-success",
  warning: "text-warning",
  danger: "text-destructive",
  accent: "text-accent",
  muted: "text-muted-foreground",
};

function checkTone(result: RunCheckOutcome): string {
  return CHECK_TONE[result.tone] ?? "text-muted-foreground";
}

/** Quality outcomes recorded against this run's attempt, plus the blocking
 * failure detail when one exists. */
export function QualityReport({ report }: { report: RunQualityReport }) {
  const passed = report.results.filter((r) => r.outcome.startsWith("Executed · passed")).length;
  const failed = report.results.filter((r) => r.outcome.startsWith("Executed · failed")).length;
  const failure = report.blocking_failure;
  return (
    <section>
      <SectionHeader
        title="Quality"
        meta={`${report.results.length} checks · ${passed} passed · ${failed} failed${
          report.attempt !== null ? ` · attempt ${report.attempt}` : ""
        }`}
      />
      {failure ? (
        <div className="overflow-clip rounded-[7px] border border-border">
          <div className="bg-destructive-soft px-3 py-2.5">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold">{failure.check}</span>
              <span className="text-[11px] leading-3.5 text-destructive">{failure.verdict}</span>
            </div>
            <p className="mt-1 text-[11px] leading-3.5 text-muted-foreground">{failure.detail}</p>
          </div>
          {failure.sample.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-37.5">key</TableHead>
                  <TableHead className="flex-1">record_id</TableHead>
                  <TableHead className="w-45">observed_at</TableHead>
                  <TableHead className="w-28 text-right">Occurrences</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {failure.sample.map((row) => (
                  <TableRow key={row.record_id}>
                    <TableCell>{row.key}</TableCell>
                    <TableCell className="text-muted-foreground">{row.record_id}</TableCell>
                    <TableCell className="text-muted-foreground">{row.observed_at}</TableCell>
                    <TableCell className="text-right text-destructive">{row.occurrences}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : null}
          <div className="flex items-center justify-between border-t border-border px-3 py-2 text-[10px] text-muted-foreground">
            <span>
              {failure.sample_total} failed row{failure.sample_total === 1 ? "" : "s"} recorded
            </span>
          </div>
        </div>
      ) : null}
      {report.results.length > 0 ? (
        <div className="mt-2 overflow-clip rounded-[7px] border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="flex-1">Check</TableHead>
                <TableHead className="w-40">Asset</TableHead>
                <TableHead className="w-16">Attempt</TableHead>
                <TableHead className="w-44">Outcome</TableHead>
                <TableHead className="w-28 text-right">Failed rows</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {report.results.map((result) => (
                <TableRow key={`${result.check}-${result.attempt}`}>
                  <TableCell className="font-medium">{result.check}</TableCell>
                  <TableCell className="text-muted-foreground">{result.asset ?? "—"}</TableCell>
                  <TableCell className="text-muted-foreground">{result.attempt}</TableCell>
                  <TableCell className={checkTone(result)}>{result.outcome}</TableCell>
                  <TableCell className="text-right text-muted-foreground">
                    {result.failed ?? "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : null}
      {!failure && report.results.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          No quality results recorded for this run.
        </p>
      ) : null}
    </section>
  );
}

/** Identity join: logical run id, provider run id, attempt and report id —
 * each labelled with the record it came from. */
export function RunIdentityBlock({ identity }: { identity: RunIdentity }) {
  const rows: Array<[string, string | null]> = [
    ["Run", identity.run_id],
    ["Durable record", identity.durable_run_id],
    ["Provider run", identity.provider_run_id],
    ["Attempt", identity.attempt !== null ? String(identity.attempt) : null],
    ["Report", identity.report],
    ["Operation", identity.operation_id],
    ["Launch digest", identity.launch_digest],
    ["Pipeline", identity.pipeline],
    ["Assets", identity.asset_ids.length ? identity.asset_ids.join(", ") : null],
  ];
  return (
    <section>
      <SectionHeader title="Identity" />
      <div className="overflow-clip rounded-[7px] border border-border px-3 py-2">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-4 py-1 text-[11px]">
            <span className="text-muted-foreground">{label}</span>
            {value ? (
              <Identifier value={value} head={14} className="text-[10.5px]" />
            ) : (
              <span className="font-mono text-[10.5px]">—</span>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

/** Most recent log lines, colour-coded by level. */
export function KeyEvents({
  events,
  onOpenLogs,
}: {
  events: Array<RunEvent>;
  onOpenLogs?: () => void;
}) {
  return (
    <section className="flex flex-col">
      <SectionHeader
        title="Key events"
        action={<InlineLink onClick={onOpenLogs}>Open full logs</InlineLink>}
      />
      <div className="flex flex-col gap-1.75">
        {events.map((event) => (
          <div key={event.at + event.message} className="flex items-center">
            <span className="w-24 shrink-0 text-[11px] leading-3.5 text-muted-foreground">
              {formatTimestamp(event.at)}
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
export function TraceWaterfall({ spans }: { spans: Array<RunSpan> }) {
  return (
    <section>
      <SectionHeader title="Traces" meta={`${spans.length} span${spans.length === 1 ? "" : "s"}`} />
      <div className="overflow-clip rounded-[7px] border border-border">
        {spans.map((span, index) => (
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
