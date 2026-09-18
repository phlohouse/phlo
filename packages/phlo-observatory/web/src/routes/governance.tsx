/**
 * Governance route: publication reviews, access drift, ownership gaps and the audit trail.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";

import type {Metric} from "@/components/metric-strip";
import {  MetricStrip } from "@/components/metric-strip";
import { ExampleDataChip, PageHeader } from "@/components/page-header";
import { PropertyList } from "@/components/property-list";
import { InlineLink, SectionHeader } from "@/components/section-header";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  accessDrift,
  auditActivity,
  governanceMetrics,
  ownershipGaps,
  publicationReviews,
  publishShipments,
} from "@/data/demo";
import { cn } from "@/lib/utils";

const METRICS: Array<Metric> = governanceMetrics;

const VERDICT_TONE: Record<string, string> = {
  Ready: "text-success",
  Blocked: "text-warning",
};

/** Promotion requests awaiting a policy verdict. */
function PublicationReviews() {
  return (
    <section className="flex flex-col gap-2.5">
      <SectionHeader
        title="Publication reviews"
        meta="3 requests · Core policy verdicts · Observed 09:35 UTC"
      />
      <div className="overflow-clip rounded-[7px] border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-52.5">Dataset</TableHead>
              <TableHead className="w-37.5">Owner</TableHead>
              <TableHead className="w-42.5">Contract</TableHead>
              <TableHead className="w-27.5">Verdict</TableHead>
              <TableHead>Reason</TableHead>
              <TableHead className="w-17.5 text-right" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {publicationReviews.map((review) => (
              <TableRow key={review.dataset} selected={review.selected}>
                <TableCell className="font-medium">{review.dataset}</TableCell>
                <TableCell>{review.owner}</TableCell>
                <TableCell>{review.contract}</TableCell>
                <TableCell className={VERDICT_TONE[review.verdict]}>{review.verdict}</TableCell>
                <TableCell>{review.reason}</TableCell>
                <TableCell className="text-right text-accent-foreground">{review.action}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

/** Declared policy vs compiled vs verified backend grants. */
function AccessDrift() {
  return (
    <section className="flex flex-col gap-2.5">
      <SectionHeader
        title={accessDrift.title}
        action={<span className="text-[11px] text-destructive">{accessDrift.verdict}</span>}
      />
      <p className="text-[11px] leading-3.5 text-muted-foreground">{accessDrift.subtitle}</p>
      <div className="overflow-clip rounded-[7px] border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-67.5">Evidence</TableHead>
              <TableHead>Effective permissions</TableHead>
              <TableHead className="w-36.25 text-right">Result</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {accessDrift.rows.map((row) => (
              <TableRow key={row.evidence} className={cn(row.drift && "bg-destructive-soft")}>
                <TableCell className="font-medium">{row.evidence}</TableCell>
                <TableCell>{row.permissions}</TableCell>
                <TableCell
                  className={cn("text-right", row.drift ? "text-destructive" : "text-muted-foreground")}
                >
                  {row.result}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <div className="flex items-center justify-between gap-4">
        <span className="text-[11px] leading-3.5 text-muted-foreground">
          Review the grant change before synchronizing policy.
        </span>
        <InlineLink>Preview grant reconciliation</InlineLink>
      </div>
    </section>
  );
}

/** Datasets missing an ownership or contract requirement. */
function OwnershipGaps() {
  return (
    <section className="flex flex-col gap-2.5">
      <SectionHeader title="Ownership & contract gaps" action={<InlineLink>View all gaps</InlineLink>} />
      <div className="overflow-clip rounded-[7px] border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-47.5">Dataset</TableHead>
              <TableHead>Missing requirement</TableHead>
              <TableHead className="w-30">Owner</TableHead>
              <TableHead className="w-35 text-right" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {ownershipGaps.map((gap) => (
              <TableRow key={gap.dataset}>
                <TableCell className="font-medium">{gap.dataset}</TableCell>
                <TableCell>{gap.requirement}</TableCell>
                <TableCell className="text-muted-foreground">{gap.owner}</TableCell>
                <TableCell className="text-right whitespace-nowrap text-accent-foreground">
                  {gap.action}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

/** Publication plan for the selected dataset with a guarded preview action. */
function PublishShipmentsRail() {
  return (
    <aside className="flex w-75 shrink-0 flex-col gap-4.5 border-l border-border pl-4.5">
      <section className="flex flex-col gap-2.5">
        <SectionHeader
          title="Publish Shipments"
          action={<span className="text-[11px] text-success">Ready</span>}
        />
        <p className="text-[11px] leading-4 text-muted-foreground">
          Review the internal Dataset publication transition.
        </p>
        <PropertyList rows={publishShipments} divided={false} />
        <p className="text-[11px] leading-4 text-muted-foreground">
          The plan rechecks state version 7, permissions, and the policy verdict before applying.
        </p>
        <Button className="w-full">Preview Dataset publication</Button>
        <p className="text-[11px] leading-4 text-muted-foreground">
          Changes Dataset publication state. Does not release data or change backend grants.
        </p>
      </section>
    </aside>
  );
}

/** Immutable record of governance-relevant actions. */
function RecentAudit() {
  return (
    <section className="flex flex-col gap-2.5">
      <SectionHeader title="Recent audit activity" action={<InlineLink>View audit log</InlineLink>} />
      <div className="overflow-clip rounded-[7px] border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-30">Time (UTC)</TableHead>
              <TableHead className="w-47.5">Actor</TableHead>
              <TableHead>Action</TableHead>
              <TableHead className="w-57.5">Target</TableHead>
              <TableHead className="w-42.5 text-right">Outcome</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {auditActivity.map((event) => (
              <TableRow key={event.time}>
                <TableCell className="text-muted-foreground">{event.time}</TableCell>
                <TableCell>{event.actor}</TableCell>
                <TableCell>{event.action}</TableCell>
                <TableCell>{event.target}</TableCell>
                <TableCell className={cn("text-right", event.tone)}>{event.outcome}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

function GovernancePage() {
  const navigate = useNavigate();
  return (
    <div className="flex flex-col">
      <div className="px-6">
        <PageHeader
          title="Governance"
          titleAccessory={<ExampleDataChip />}
          description="See who owns the data, who can access it, and what is ready to publish."
          actions={
            <>
              <Button variant="outline">View audit log</Button>
              <Button>Review access drift</Button>
            </>
          }
        />
      </div>

      <div className="px-6 pb-4">
        <MetricStrip metrics={METRICS} dividers={false} />
      </div>

      <Tabs defaultValue="overview" className="gap-0">
        <div className="border-b border-border px-6">
          <TabsList className="border-b-0">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="ownership">Ownership &amp; contracts</TabsTrigger>
            <TabsTrigger value="access">Access policies</TabsTrigger>
            <TabsTrigger value="reviews">Publication reviews · 3</TabsTrigger>
            <TabsTrigger value="audit">Audit</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="overview">
          <div className="flex flex-col gap-4.5 px-6 pt-4.5 pb-5">
            <PublicationReviews />
            <div className="flex gap-5.5">
              <div className="flex min-w-0 flex-1 flex-col gap-4.5">
                <AccessDrift />
                <OwnershipGaps />
              </div>
              <PublishShipmentsRail />
            </div>
            <RecentAudit />
          </div>
        </TabsContent>

        <TabsContent value="ownership">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="min-w-0 flex-1">
              <OwnershipGaps />
            </div>
            <PublishShipmentsRail />
          </div>
        </TabsContent>

        <TabsContent value="access">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="min-w-0 flex-1">
              <AccessDrift />
            </div>
            <PublishShipmentsRail />
          </div>
        </TabsContent>

        <TabsContent value="reviews">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="min-w-0 flex-1">
              <PublicationReviews />
            </div>
            <PublishShipmentsRail />
          </div>
        </TabsContent>

        <TabsContent value="audit">
          <div className="flex flex-col gap-4.5 px-6 pt-4.5 pb-5">
            <RecentAudit />
            <div className="flex gap-2">
              <InlineLink onClick={() => navigate({ to: "/governance" })}>Export audit log</InlineLink>
            </div>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export const Route = createFileRoute("/governance")({ component: GovernancePage });
