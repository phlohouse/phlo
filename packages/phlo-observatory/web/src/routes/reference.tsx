/**
 * Reference route: live design-system reference for tokens, type, status
 * vocabulary and core components.
 */
import { createFileRoute } from "@tanstack/react-router";

import { StatusPill } from "@/components/data/status-pill";
import { TypeScaleTable } from "@/components/foundation/type-scale";
import { Page, PageStack } from "@/components/layout/page";
import { ExampleDataChip, PageHeader } from "@/components/layout/page-header";
import { Section } from "@/components/layout/section-header";
import {
  ColorTokenGrid,
  ComponentCardGrid,
  RuleCardGrid,
} from "@/components/sections/reference/reference-blocks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

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

const TYPE_SAMPLES = [
  {
    token: "25px / 600",
    label: "Page title",
    className: "font-display text-[25px] leading-7.25 font-semibold tracking-[-0.03em]",
  },
  {
    token: "17px / 600",
    label: "Metric value",
    className: "font-display text-[17px] leading-5.5 font-semibold tracking-[-0.02em]",
  },
  { token: "15px / 600", label: "Section heading", className: "font-display text-[15px] leading-4.5 font-semibold" },
  { token: "13px / 600", label: "Row title", className: "text-[13px] leading-4 font-semibold" },
  { token: "12px / 400", label: "Body and table cell", className: "text-xs leading-4" },
  { token: "11px / 500", label: "Table header, meta", className: "text-[11px] leading-3.5 font-medium" },
  { token: "10px / 400", label: "Micro caption", className: "text-[10px] leading-3" },
];

const STATUS_SAMPLES = [
  "Ready",
  "Connected · 42ms",
  "Fresh",
  "Complete",
  "Pending",
  "Ready for review",
  "Blocked by quality",
  "35m late",
  "Failed validation",
  "Unreachable · timing out",
  "Not started",
];

const CORE_COMPONENTS = [
  { name: "Primary button", hint: "One per view — the highest-intent action", preview: <Button>Create workflow</Button> },
  { name: "Outline button", hint: "Secondary controls and scope pickers", preview: <Button variant="outline">Last 24 hours</Button> },
  { name: "Ghost button", hint: "Toolbar and icon affordances", preview: <Button variant="ghost">Cancel</Button> },
  { name: "Status pill", hint: "Derives tone from backend status text", preview: <StatusPill status="Ready" /> },
  { name: "Blocked pill", hint: "Failure vocabulary maps to destructive", preview: <StatusPill status="Blocked by quality" /> },
  { name: "Neutral badge", hint: "Counts, classification, non-status metadata", preview: <Badge variant="muted">Internal</Badge> },
];

const USAGE_RULES = [
  { title: "Never merge the three states", body: "Execution, evidence and release state are reported separately and may legitimately disagree." },
  { title: "Status text drives colour", body: "Do not hard-code tones in pages — pass the backend status to StatusPill so vocabulary stays consistent." },
  { title: "One primary action per view", body: "Secondary intent uses outline or ghost so the primary path is unambiguous." },
  { title: "Numbers are tabular", body: "Counts and durations use Roboto Mono where column alignment matters." },
];

function ReferencePage() {
  return (
    <Page>
      <PageHeader
        title="Reference"
        titleAccessory={<ExampleDataChip />}
        description="Shared building blocks as used across Mission Control — tokens, type, status vocabulary and core components."
      />
      <PageStack className="w-290 max-w-full gap-5 px-6 pt-4.5 pb-5">
        <Section title="Colour tokens">
          <ColorTokenGrid tokens={COLOR_TOKENS} />
        </Section>
        <Section title="Type scale">
          <TypeScaleTable samples={TYPE_SAMPLES} />
        </Section>
        <Section title="Status indicators" meta="Soft-fill badges carry a leading dot">
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-3.5">
            {STATUS_SAMPLES.map((status) => (
              <StatusPill key={status} status={status} />
            ))}
          </div>
        </Section>
        <Section title="Core components">
          <ComponentCardGrid items={CORE_COMPONENTS} />
        </Section>
        <Section title="Usage rules">
          <RuleCardGrid items={USAGE_RULES} />
        </Section>
      </PageStack>
    </Page>
  );
}

export const Route = createFileRoute("/reference")({ component: ReferencePage });
