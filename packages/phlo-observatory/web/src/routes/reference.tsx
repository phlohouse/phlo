/**
 * Reference route: live design-system reference for tokens, type, status and components.
 */
import { createFileRoute } from "@tanstack/react-router";

import { ExampleDataChip, PageHeader } from "@/components/page-header";
import { StatusPill } from "@/components/status-pill";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

/**
 * Design-system reference: the tokens, type scale, status vocabulary and core
 * components that every Mission Control screen is built from.
 *
 * Swatches read the live CSS variables, so this page stays truthful as the
 * token layer evolves.
 */
const COLOR_TOKENS = [
  { name: "--background", usage: "App canvas behind the workspace card" },
  { name: "--card", usage: "Workspace card and section surfaces" },
  { name: "--foreground", usage: "Primary text" },
  { name: "--muted-foreground", usage: "Secondary text, labels, meta" },
  { name: "--border", usage: "Hairline separators and card outlines" },
  { name: "--primary", usage: "Primary action fill, brand violet" },
  { name: "--primary-strong", usage: "Accent text and links" },
  { name: "--accent", usage: "Table header band, active navigation" },
  { name: "--secondary", usage: "Attention rows and quiet fills" },
  { name: "--subtle", usage: "Metric strip and inset surfaces" },
  { name: "--success", usage: "Ready, passed, connected" },
  { name: "--warning", usage: "Delayed, blocked, degraded" },
  { name: "--destructive", usage: "Failed, drift, unreachable" },
];

const TYPE_SCALE = [
  { token: "25px / 600", sample: "Page title", className: "font-display text-[25px] leading-7.25 font-semibold tracking-[-0.03em]" },
  { token: "17px / 600", sample: "Metric value", className: "font-display text-[17px] leading-5.5 font-semibold tracking-[-0.02em]" },
  { token: "15px / 600", sample: "Section heading", className: "font-display text-[15px] leading-4.5 font-semibold" },
  { token: "13px / 600", sample: "Row title", className: "text-[13px] leading-4 font-semibold" },
  { token: "12px / 400", sample: "Body and table cell", className: "text-xs leading-4" },
  { token: "11px / 500", sample: "Table header, meta", className: "text-[11px] leading-3.5 font-medium" },
  { token: "10px / 400", sample: "Micro caption", className: "text-[10px] leading-3" },
];

const CORE_COMPONENTS = [
  { name: "Primary button", hint: "One per view — the highest-intent action", node: <Button>Create workflow</Button> },
  { name: "Outline button", hint: "Secondary controls and scope pickers", node: <Button variant="outline">Last 24 hours</Button> },
  { name: "Ghost button", hint: "Toolbar and icon affordances", node: <Button variant="ghost">Cancel</Button> },
  { name: "Status pill", hint: "Derives tone from backend status text", node: <StatusPill status="Ready" /> },
  { name: "Blocked pill", hint: "Failure vocabulary maps to destructive", node: <StatusPill status="Blocked by quality" /> },
  { name: "Neutral badge", hint: "Counts, classification, non-status metadata", node: <Badge variant="muted">Internal</Badge> },
];

const USAGE_RULES = [
  { title: "Never merge the three states", body: "Execution, evidence and release state are reported separately and may legitimately disagree." },
  { title: "Status text drives colour", body: "Do not hard-code tones in pages — pass the backend status to StatusPill so vocabulary stays consistent." },
  { title: "One primary action per view", body: "Secondary intent uses outline or ghost so the primary path is unambiguous." },
  { title: "Numbers are tabular", body: "Counts and durations use Roboto Mono where column alignment matters." },
];

function ReferencePage() {
  return (
    <div className="flex flex-col">
      <div className="px-6">
        <PageHeader
          title="Reference"
          titleAccessory={<ExampleDataChip />}
          description="Shared building blocks as used across Mission Control — tokens, type, status vocabulary and core components."
        />
      </div>

      <div className="flex w-290 max-w-full flex-col gap-5 px-6 pt-4.5 pb-5">
        <section className="flex flex-col gap-2.5">
          <h2 className="text-sm leading-5 font-semibold">Colour tokens</h2>
          <div className="grid grid-cols-3 gap-3.5 xl:grid-cols-4">
            {COLOR_TOKENS.map((token) => (
              <div key={token.name} className="flex items-center gap-2.5 rounded-lg border border-border bg-card p-2.5">
                <span
                  className="size-8 shrink-0 rounded-md border border-border"
                  style={{ background: `var(${token.name})` }}
                />
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="truncate font-mono text-[11px]">{token.name}</span>
                  <span className="truncate text-[10px] leading-3 text-muted-foreground">
                    {token.usage}
                  </span>
                </span>
              </div>
            ))}
          </div>
        </section>

        <section className="flex flex-col gap-2.5">
          <h2 className="text-sm leading-5 font-semibold">Type scale</h2>
          <div className="overflow-clip rounded-[7px] border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-40">Token</TableHead>
                  <TableHead>Sample</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {TYPE_SCALE.map((row) => (
                  <TableRow key={row.token}>
                    <TableCell className="font-mono text-[11px] text-muted-foreground">
                      {row.token}
                    </TableCell>
                    <TableCell>
                      <span className={row.className}>{row.sample}</span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </section>

        <section className="flex flex-col gap-2.5">
          <h2 className="text-sm leading-5 font-semibold">Status indicators</h2>
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-3.5">
            <StatusPill status="Ready" />
            <StatusPill status="Connected · 42ms" />
            <StatusPill status="Fresh" />
            <StatusPill status="Complete" />
            <StatusPill status="Pending" />
            <StatusPill status="Ready for review" />
            <StatusPill status="Blocked by quality" />
            <StatusPill status="35m late" />
            <StatusPill status="Failed validation" />
            <StatusPill status="Unreachable · timing out" />
            <StatusPill status="Not started" />
          </div>
          <p className="text-[11px] leading-3.5 text-muted-foreground">
            Status text is mapped to four tones by <code className="font-mono">statusTone</code> —
            soft-fill badges carry a leading dot; the same tones drive dots in lists and tables.
          </p>
        </section>

        <section className="flex flex-col gap-2.5">
          <h2 className="text-sm leading-5 font-semibold">Core components</h2>
          <div className="grid grid-cols-3 gap-3.5">
            {CORE_COMPONENTS.map((component) => (
              <div key={component.name} className="flex flex-col gap-2.5 rounded-lg border border-border bg-card p-3.5">
                <div className="flex h-8 items-center">{component.node}</div>
                <div className="flex flex-col gap-0.5">
                  <span className="text-[13px] leading-4.5 font-semibold">{component.name}</span>
                  <span className="text-[11px] leading-3.75 text-muted-foreground">
                    {component.hint}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="flex flex-col gap-2.5">
          <h2 className="text-sm leading-5 font-semibold">Usage rules</h2>
          <div className="grid grid-cols-2 gap-3.5">
            {USAGE_RULES.map((rule) => (
              <div key={rule.title} className="flex flex-col gap-1.5 rounded-lg border border-border bg-card p-3.5">
                <span className="text-[13px] leading-4.5 font-semibold">{rule.title}</span>
                <span className="text-xs leading-4.25 text-muted-foreground">{rule.body}</span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

export const Route = createFileRoute("/reference")({ component: ReferencePage });
