/**
 * Settings route: provider connections, notifications, members and defaults.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import type {Metric} from "@/components/data/metric-strip";
import { queries } from "@/api/mission-control";

import {  MetricStrip } from "@/components/data/metric-strip";
import { Banner } from "@/components/feedback/banner";
import { Page, PageBand, PageContent, PageStack } from "@/components/layout/page";
import { ExampleDataChip, PageHeader } from "@/components/layout/page-header";
import { PageTabs, TabsContent } from "@/components/layout/page-tabs";
import { InlineLink, Section } from "@/components/layout/section-header";
import { ProviderConnections } from "@/components/sections/settings/provider-connections";
import { SettingsTable } from "@/components/sections/settings/settings-table";
import { Button } from "@/components/ui/button";

const TABS = [
  { value: "providers", label: "Provider connections" },
  { value: "notifications", label: "Notifications" },
  { value: "members", label: "Members & roles" },
  { value: "defaults", label: "Defaults" },
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

const TONE_CLASS: Record<string, string | undefined> = {
  success: "text-success",
  warning: "text-warning",
  danger: "text-destructive",
  accent: "text-accent-foreground",
  muted: undefined,
};

function SettingsPage() {
  const summary = useQuery(queries.settingsSummary());
  const providers = useQuery(queries.providerConnections());
  const impact = useQuery(queries.providerImpact("polaris"));
  const notifications = useQuery(queries.notificationRules());
  const members = useQuery(queries.workspaceMembers());
  const defaults = useQuery(queries.workspaceDefaults());

  const metrics: Array<Metric> = (summary.data ?? []).map((metric) => ({
    label: metric.label,
    value: metric.value,
    hint: metric.hint,
    tone: TONE_CLASS[metric.tone],
  }));

  const notificationRows = (notifications.data ?? []).map((rule) => ({
    name: rule.name,
    value: rule.channel,
    meta: rule.urgency,
  }));
  const memberRows = (members.data ?? []).map((member) => ({
    name: member.name,
    value: member.role,
    meta: member.status === "active" ? "Active" : "Invited",
    tone: member.status === "active" ? "text-success" : "text-warning",
  }));
  const defaultRows = (defaults.data ?? []).map((row) => ({ name: row.name, value: row.value }));

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
        <MetricStrip metrics={metrics} dividers={false} />
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
              {providers.data ? (
                <ProviderConnections
                  connections={providers.data}
                  title={`Connections · ${providers.data.length}`}
                  meta="Checks run every 60s · outcomes recorded in Audit"
                />
              ) : null}
              {impact.data ? (
                <div className="flex gap-4">
                  <ImpactCard
                    title={`Degraded while ${impact.data.provider} is down`}
                    items={impact.data.degraded}
                  />
                  <ImpactCard title="Unaffected" items={impact.data.unaffected} />
                </div>
              ) : null}
            </PageStack>
          </PageContent>
        </TabsContent>

        <TabsContent value="notifications">
          <PageContent>
            <Section title="Notification rules" meta="5 rules · 3 channels">
              <SettingsTable rows={notificationRows} />
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
              <SettingsTable rows={memberRows} />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="defaults">
          <PageContent>
            <Section title="Defaults" meta="Applied across the workspace">
              <SettingsTable rows={defaultRows} />
            </Section>
          </PageContent>
        </TabsContent>
      </PageTabs>
    </Page>
  );
}

export const Route = createFileRoute("/settings")({ component: SettingsPage });
