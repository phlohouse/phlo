/**
 * Governance route: publication reviews, access drift, ownership gaps and the
 * audit trail. All data comes from the mission control read models.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import type {Metric} from "@/components/data/metric-strip";
import { queries } from "@/api/mission-control";
import {  MetricStrip } from "@/components/data/metric-strip";
import { Page, PageBand, PageContent, PageStack, SplitRow } from "@/components/layout/page";
import { PageHeader, ReadStateChip } from "@/components/layout/page-header";
import { PageTabs, TabsContent } from "@/components/layout/page-tabs";
import { Section } from "@/components/layout/section-header";
import { AccessDriftPanel } from "@/components/sections/governance/access-drift-panel";
import { AuditTable } from "@/components/sections/governance/audit-table";
import { OwnershipGapsTable } from "@/components/sections/governance/ownership-gaps-table";
import { PublicationReviewsTable } from "@/components/sections/governance/publication-reviews-table";

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "ownership", label: "Ownership & contracts" },
  { value: "access", label: "Access policies" },
  { value: "reviews", label: "Publication reviews" },
  { value: "audit", label: "Audit" },
];

const TONE_CLASS: Record<string, string | undefined> = {
  success: "text-success",
  warning: "text-warning",
  danger: "text-destructive",
  accent: "text-accent-foreground",
  muted: undefined,
};

function GovernancePage() {
  const summary = useQuery(queries.governanceSummary());
  const reviews = useQuery(queries.publicationReviews());
  const drift = useQuery(queries.accessDrift());
  const gaps = useQuery(queries.ownershipGaps());
  const audit = useQuery(queries.auditEvents());

  const metrics: Array<Metric> = (summary.data?.data ?? []).map((metric) => ({
    label: metric.label,
    value: metric.value,
    hint: metric.hint,
    tone: TONE_CLASS[metric.tone],
  }));

  const driftPanels = (drift.data?.data ?? []).map((entry) => (
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
      meta={`${reviews.data?.data?.length ?? 0} requests · Core policy verdicts`}
    >
      {reviews.data?.data ? <PublicationReviewsTable rows={reviews.data.data} /> : null}
    </Section>
  );

  const gapsSection = (
    <Section
      title="Ownership & contract gaps"
      meta={`${gaps.data?.data?.length ?? 0} gaps`}
    >
      {gaps.data?.data ? <OwnershipGapsTable rows={gaps.data.data} /> : null}
    </Section>
  );

  const auditSection = (
    <Section
      title="Recent audit activity"
      meta={`${audit.data?.data?.length ?? 0} events`}
    >
      {audit.data?.data ? <AuditTable rows={audit.data.data} /> : null}
    </Section>
  );

  return (
    <Page>
      <PageHeader
        title="Governance"
        titleAccessory={<ReadStateChip evidence={summary.data?.evidence} />}
        description="See who owns the data, who can access it, and what is ready to publish."
      />

      <PageBand>
        <MetricStrip metrics={metrics} dividers={false} />
      </PageBand>

      <PageTabs tabs={TABS} defaultValue="overview">
        <TabsContent value="overview">
          <PageContent>
            {reviewsSection}
            <SplitRow>
              {driftPanels}
              {gapsSection}
            </SplitRow>
            {auditSection}
          </PageContent>
        </TabsContent>

        <TabsContent value="ownership">
          <PageContent>{gapsSection}</PageContent>
        </TabsContent>

        <TabsContent value="access">
          <PageContent>{driftPanels}</PageContent>
        </TabsContent>

        <TabsContent value="reviews">
          <PageContent>{reviewsSection}</PageContent>
        </TabsContent>

        <TabsContent value="audit">
          <PageContent>
            <PageStack>
              <Section
                title="Audit"
                meta={`${audit.data?.data?.length ?? 0} events recorded`}
              >
                {audit.data?.data ? <AuditTable rows={audit.data.data} /> : null}
              </Section>
            </PageStack>
          </PageContent>
        </TabsContent>
      </PageTabs>
    </Page>
  );
}

export const Route = createFileRoute("/governance")({ component: GovernancePage });
