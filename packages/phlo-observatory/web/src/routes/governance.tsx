/**
 * Governance route: publication reviews, access drift, ownership gaps and the
 * audit trail. All data comes from the mission control read models.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import type {Metric} from "@/components/data/metric-strip";
import { queries } from "@/api/mission-control";
import { EvidenceListPlain } from "@/components/data/evidence-list";
import {  MetricStrip } from "@/components/data/metric-strip";
import { StatusPill } from "@/components/data/status-pill";
import { DetailRail, DetailSection } from "@/components/layout/detail-rail";
import { Page, PageBand, PageContent, PageStack, SplitRow } from "@/components/layout/page";
import { ExampleDataChip, PageHeader } from "@/components/layout/page-header";
import { PageTabs, TabsContent } from "@/components/layout/page-tabs";
import { InlineLink, Section } from "@/components/layout/section-header";
import { AccessDriftPanel } from "@/components/sections/governance/access-drift-panel";
import { AuditTable } from "@/components/sections/governance/audit-table";
import { OwnershipGapsTable } from "@/components/sections/governance/ownership-gaps-table";
import { PublicationReviewsTable } from "@/components/sections/governance/publication-reviews-table";
import { Button } from "@/components/ui/button";

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "ownership", label: "Ownership & contracts" },
  { value: "access", label: "Access policies" },
  { value: "reviews", label: "Publication reviews · 3" },
  { value: "audit", label: "Audit" },
];

const TONE_CLASS: Record<string, string | undefined> = {
  success: "text-success",
  warning: "text-warning",
  danger: "text-destructive",
  accent: "text-accent-foreground",
  muted: undefined,
};

/** Publication plan for the selected dataset, with a guarded preview action. */
function PublicationPlanRail({ datasetId }: { datasetId: string }) {
  const plan = useQuery(queries.publicationPlan(datasetId));
  if (!plan.data) return null;
  return (
    <DetailRail>
      <DetailSection title={`Publish ${plan.data.dataset_id}`} action={<StatusPill status={plan.data.status} tone="success" />}>
        <p className="text-[11px] leading-4 text-muted-foreground">{plan.data.subtitle}</p>
        <EvidenceListPlain rows={plan.data.rows} />
        <p className="text-[11px] leading-4 text-muted-foreground">
          The plan rechecks the expected state version, permissions, and the policy verdict before
          applying.
        </p>
        <Button className="w-full">Preview Dataset publication</Button>
        <p className="text-[11px] leading-4 text-muted-foreground">
          Changes Dataset publication state. Does not release data or change backend grants.
        </p>
      </DetailSection>
    </DetailRail>
  );
}

function GovernancePage() {
  const summary = useQuery(queries.governanceSummary());
  const reviews = useQuery(queries.publicationReviews());
  const drift = useQuery(queries.accessDrift());
  const gaps = useQuery(queries.ownershipGaps());
  const audit = useQuery(queries.auditEvents());

  const metrics: Array<Metric> = (summary.data ?? []).map((metric) => ({
    label: metric.label,
    value: metric.value,
    hint: metric.hint,
    tone: TONE_CLASS[metric.tone],
  }));

  const rail = <PublicationPlanRail datasetId="logistics.shipments" />;

  const driftPanels = (drift.data ?? []).map((entry) => (
    <AccessDriftPanel
      key={entry.dataset}
      title={`Access drift · ${entry.dataset}`}
      verdict={entry.verdict}
      subtitle={entry.subtitle}
      rows={entry.evidence}
      note="Review the grant change before synchronizing policy."
      action="Preview grant reconciliation"
    />
  ));

  const reviewsSection = (
    <Section
      title="Publication reviews"
      meta="3 requests · Core policy verdicts · Observed 09:35 UTC"
    >
      {reviews.data ? <PublicationReviewsTable rows={reviews.data} /> : null}
    </Section>
  );

  const gapsSection = (
    <Section title="Ownership & contract gaps" action={<InlineLink>View all gaps</InlineLink>}>
      {gaps.data ? <OwnershipGapsTable rows={gaps.data} /> : null}
    </Section>
  );

  const auditSection = (
    <Section title="Recent audit activity" action={<InlineLink>View audit log</InlineLink>}>
      {audit.data ? <AuditTable rows={audit.data} /> : null}
    </Section>
  );

  return (
    <Page>
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

      <PageBand>
        <MetricStrip metrics={metrics} dividers={false} />
      </PageBand>

      <PageTabs tabs={TABS} defaultValue="overview">
        <TabsContent value="overview">
          <PageContent>
            {reviewsSection}
            <SplitRow rail={rail}>
              {driftPanels}
              {gapsSection}
            </SplitRow>
            {auditSection}
          </PageContent>
        </TabsContent>

        <TabsContent value="ownership">
          <PageContent rail={rail}>{gapsSection}</PageContent>
        </TabsContent>

        <TabsContent value="access">
          <PageContent rail={rail}>{driftPanels}</PageContent>
        </TabsContent>

        <TabsContent value="reviews">
          <PageContent rail={rail}>{reviewsSection}</PageContent>
        </TabsContent>

        <TabsContent value="audit">
          <PageContent>
            <PageStack>
              <Section
                title="Audit"
                meta="18 events in the last 24 hours"
                action={<InlineLink>Export audit log</InlineLink>}
              >
                {audit.data ? <AuditTable rows={audit.data} /> : null}
              </Section>
            </PageStack>
          </PageContent>
        </TabsContent>
      </PageTabs>
    </Page>
  );
}

export const Route = createFileRoute("/governance")({ component: GovernancePage });
