/**
 * Governance route: publication reviews, access drift, ownership gaps and the
 * audit trail. Section implementations live under `components/sections/governance`.
 */
import { createFileRoute } from "@tanstack/react-router";

import type {Metric} from "@/components/data/metric-strip";
import { EvidenceListPlain } from "@/components/data/evidence-list";
import {  MetricStrip } from "@/components/data/metric-strip";
import { StatusPill } from "@/components/data/status-pill";
import { Page, PageBand, PageContent, PageStack } from "@/components/layout/page";
import { ExampleDataChip, PageHeader } from "@/components/layout/page-header";
import { PageTabs, TabsContent } from "@/components/layout/page-tabs";
import { InlineLink, Section } from "@/components/layout/section-header";
import { DetailRail, DetailSection } from "@/components/layout/detail-rail";
import { AccessDriftPanel } from "@/components/sections/governance/access-drift-panel";
import { AuditTable } from "@/components/sections/governance/audit-table";
import { OwnershipGapsTable } from "@/components/sections/governance/ownership-gaps-table";
import { PublicationReviewsTable } from "@/components/sections/governance/publication-reviews-table";
import { Button } from "@/components/ui/button";
import {
  accessDrift,
  auditActivity,
  governanceMetrics,
  ownershipGaps,
  publicationReviews,
  publishShipments,
} from "@/data/demo";

const METRICS: Array<Metric> = governanceMetrics;

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "ownership", label: "Ownership & contracts" },
  { value: "access", label: "Access policies" },
  { value: "reviews", label: "Publication reviews · 3" },
  { value: "audit", label: "Audit" },
];

/** Publication plan for the selected dataset, with a guarded preview action. */
function PublicationPlanRail() {
  return (
    <DetailRail>
      <DetailSection
        title="Publish Shipments"
        action={<StatusPill status="Ready" tone="success" />}
      >
        <p className="text-[11px] leading-4 text-muted-foreground">
          Review the internal Dataset publication transition.
        </p>
        <EvidenceListPlain rows={publishShipments} />
        <p className="text-[11px] leading-4 text-muted-foreground">
          The plan rechecks state version 7, permissions, and the policy verdict before applying.
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
  const rail = <PublicationPlanRail />;

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
        <MetricStrip metrics={METRICS} dividers={false} />
      </PageBand>

      <PageTabs tabs={TABS} defaultValue="overview">
        <TabsContent value="overview">
          <PageStack className="px-6 pt-4.5 pb-5">
            <Section
              title="Publication reviews"
              meta="3 requests · Core policy verdicts · Observed 09:35 UTC"
            >
              <PublicationReviewsTable rows={publicationReviews} />
            </Section>
            <PageContent rail={rail}>
              <AccessDriftPanel
                title={accessDrift.title}
                verdict={accessDrift.verdict}
                subtitle={accessDrift.subtitle}
                rows={accessDrift.rows}
                note="Review the grant change before synchronizing policy."
                action="Preview grant reconciliation"
              />
              <Section title="Ownership & contract gaps" action={<InlineLink>View all gaps</InlineLink>}>
                <OwnershipGapsTable rows={ownershipGaps} />
              </Section>
            </PageContent>
            <Section title="Recent audit activity" action={<InlineLink>View audit log</InlineLink>}>
              <AuditTable rows={auditActivity} />
            </Section>
          </PageStack>
        </TabsContent>

        <TabsContent value="ownership">
          <PageContent rail={rail}>
            <Section title="Ownership & contract gaps" action={<InlineLink>View all gaps</InlineLink>}>
              <OwnershipGapsTable rows={ownershipGaps} />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="access">
          <PageContent rail={rail}>
            <AccessDriftPanel
              title={accessDrift.title}
              verdict={accessDrift.verdict}
              subtitle={accessDrift.subtitle}
              rows={accessDrift.rows}
              note="Review the grant change before synchronizing policy."
              action="Preview grant reconciliation"
            />
          </PageContent>
        </TabsContent>

        <TabsContent value="reviews">
          <PageContent rail={rail}>
            <Section
              title="Publication reviews"
              meta="3 requests · Core policy verdicts · Observed 09:35 UTC"
            >
              <PublicationReviewsTable rows={publicationReviews} />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="audit">
          <PageContent>
            <Section title="Audit" meta="18 events in the last 24 hours" action={<InlineLink>Export audit log</InlineLink>}>
              <AuditTable rows={auditActivity} />
            </Section>
          </PageContent>
        </TabsContent>
      </PageTabs>
    </Page>
  );
}

export const Route = createFileRoute("/governance")({ component: GovernancePage });
