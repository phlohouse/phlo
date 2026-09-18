/**
 * Settings route: provider connections, notifications, members and defaults.
 */
import { createFileRoute } from "@tanstack/react-router";

import type {Metric} from "@/components/data/metric-strip";
import {  MetricStrip } from "@/components/data/metric-strip";
import { Banner } from "@/components/feedback/banner";
import { Page, PageBand, PageContent, PageStack } from "@/components/layout/page";
import { ExampleDataChip, PageHeader } from "@/components/layout/page-header";
import { PageTabs, TabsContent } from "@/components/layout/page-tabs";
import { InlineLink, Section } from "@/components/layout/section-header";
import { ProviderConnections } from "@/components/sections/settings/provider-connections";
import { SettingsTable } from "@/components/sections/settings/settings-table";
import { Button } from "@/components/ui/button";
import {
  degradedList,
  providerConnections,
  settingsMetrics,
  unaffectedList,
} from "@/data/demo";

const METRICS: Array<Metric> = settingsMetrics;

const TABS = [
  { value: "providers", label: "Provider connections" },
  { value: "notifications", label: "Notifications" },
  { value: "members", label: "Members & roles" },
  { value: "defaults", label: "Defaults" },
];

const NOTIFICATION_ROWS = [
  { name: "Release blocked", value: "#phlo-ops", meta: "Immediate" },
  { name: "Evidence degraded", value: "#phlo-ops", meta: "Immediate" },
  { name: "Run failed", value: "#phlo-data", meta: "Immediate" },
  { name: "Nightly digest", value: "Email", meta: "Daily 08:00" },
  { name: "Maintenance window", value: "Email", meta: "Advance notice" },
];

const MEMBER_ROWS = [
  { name: "Gareth Price", value: "Workspace admin", meta: "Active", tone: "text-success" },
  { name: "data.steward", value: "Operator", meta: "Active", tone: "text-success" },
  { name: "Policy verifier", value: "Reviewer", meta: "Active", tone: "text-success" },
  { name: "Finance", value: "Viewer", meta: "Invited", tone: "text-warning" },
];

const DEFAULT_ROWS = [
  { name: "Freshness target", value: "2 hours" },
  { name: "Classification default", value: "Internal" },
  { name: "Retention", value: "7 years · cold after 1" },
  { name: "Evidence retention", value: "30 days" },
  { name: "Timezone", value: "UTC" },
];

/** Impact summary of a degraded provider. */
function ImpactCard({ title, items }: { title: string; items: Array<string> }) {
  return (
    <div className="flex flex-1 flex-col gap-2 rounded-lg border border-border bg-card p-3.5">
      <h3 className="text-[13px] leading-4.5 font-semibold">{title}</h3>
      {items.map((item) => (
        <p key={item} className="text-xs leading-4.25 text-[#525252] dark:text-muted-foreground">
          · {item}
        </p>
      ))}
    </div>
  );
}

function SettingsPage() {
  return (
    <Page>
      <PageHeader
        title="Settings"
        titleAccessory={<ExampleDataChip />}
        description="Provider connections, notifications, members, and workspace defaults"
        actions={
          <>
            <Button variant="outline">View configuration</Button>
            <Button>Run diagnostics</Button>
          </>
        }
      />

      <PageBand>
        <MetricStrip metrics={METRICS} dividers={false} />
      </PageBand>

      <PageTabs tabs={TABS} defaultValue="providers">
        <TabsContent value="providers">
          <PageContent>
            <PageStack>
              <Banner
                tone="danger"
                title="Polaris is unreachable — no response since 09:21 (14m)"
                detail="Evidence sourced from Polaris is stale and stamped “last confirmed”. Released data is unaffected: snapshot 938105 remains what consumers read. Nothing is assumed."
              />
              <ProviderConnections
                connections={providerConnections}
                title="Connections · 3"
                meta="Checks run every 60s · outcomes recorded in Audit"
              />
              <div className="flex gap-4">
                <ImpactCard title="Degraded while Polaris is down" items={degradedList} />
                <ImpactCard title="Unaffected" items={unaffectedList} />
              </div>
            </PageStack>
          </PageContent>
        </TabsContent>

        <TabsContent value="notifications">
          <PageContent>
            <Section title="Notification rules" meta="5 rules · 3 channels">
              <SettingsTable rows={NOTIFICATION_ROWS} />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="members">
          <PageContent>
            <Section
              title="Members & roles"
              meta="14 members · 4 operators · 3 reviewers"
              action={<InlineLink>Invite member</InlineLink>}
            >
              <SettingsTable rows={MEMBER_ROWS} />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="defaults">
          <PageContent>
            <Section title="Defaults" meta="Applied across the workspace">
              <SettingsTable rows={DEFAULT_ROWS} />
            </Section>
          </PageContent>
        </TabsContent>
      </PageTabs>
    </Page>
  );
}

export const Route = createFileRoute("/settings")({ component: SettingsPage });
