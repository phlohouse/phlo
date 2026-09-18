/**
 * Settings route: provider connections, notifications, members and workspace defaults.
 */
import { createFileRoute } from "@tanstack/react-router";

import type {Metric} from "@/components/metric-strip";
import {  MetricStrip } from "@/components/metric-strip";
import { ExampleDataChip, PageHeader } from "@/components/page-header";
import { SectionHeader } from "@/components/section-header";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  degradedList,
  providerConnections,
  settingsMetrics,
  unaffectedList,
} from "@/data/demo";
import { cn } from "@/lib/utils";

const METRICS: Array<Metric> = settingsMetrics;

/** Summary card listing what a degraded provider does and does not affect. */
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

/** Reachability of each provider the workspace depends on. */
function ProviderConnections() {
  return (
    <div className="flex w-290 max-w-full flex-col gap-4">
      <div className="flex items-start gap-2.5 rounded-lg border border-destructive-border bg-destructive-surface px-3.5 py-3">
        <span className="mt-1.25 size-2 shrink-0 rounded-[4px] bg-destructive" />
        <div className="flex flex-col gap-0.75">
          <span className="text-[13px] leading-4.5 font-semibold">
            Polaris is unreachable — no response since 09:21 (14m)
          </span>
          <span className="text-xs leading-4 text-[#525252] dark:text-muted-foreground">
            Evidence sourced from Polaris is stale and stamped “last confirmed”. Released data is
            unaffected: snapshot 938105 remains what consumers read. Nothing is assumed.
          </span>
        </div>
      </div>

      <div className="overflow-clip rounded-lg border border-border">
        <SectionHeader
          title="Connections · 3"
          meta="Checks run every 60s · outcomes recorded in Audit"
          className="border-b border-border bg-accent px-3.5 py-3"
        />
        <div className="flex flex-col">
          {providerConnections.map((connection, index) => (
            <div
              key={connection.name}
              className={cn(
                "flex items-center gap-3.5 px-3.5 py-3.25",
                index < providerConnections.length - 1 && "border-b border-border",
              )}
            >
              <span
                className={cn(
                  "size-2 shrink-0 rounded-[4px]",
                  connection.degraded ? "bg-destructive" : "bg-success",
                )}
              />
              <div className="flex w-54 shrink-0 flex-col gap-0.5">
                <span className="text-[13px] leading-4.5 font-semibold">{connection.name}</span>
                <span className="text-[11px] leading-3.75 text-muted-foreground">
                  {connection.role}
                </span>
              </div>
              <span className="w-65.5 shrink-0 truncate font-mono text-[11px] leading-3.75 text-[#525252] dark:text-muted-foreground">
                {connection.endpoint}
              </span>
              <div className="flex flex-1 flex-col gap-0.5">
                <span
                  className={cn(
                    "text-xs leading-4",
                    connection.degraded ? "text-destructive" : "text-success",
                  )}
                >
                  {connection.state}
                </span>
                <span className="text-[11px] leading-3.75 text-muted-foreground">
                  {connection.detail}
                </span>
              </div>
              <Button variant="outline" size="sm" className="shrink-0 rounded-md text-accent-foreground">
                {connection.action}
              </Button>
            </div>
          ))}
        </div>
      </div>

      <div className="flex gap-4">
        <ImpactCard title="Degraded while Polaris is down" items={degradedList} />
        <ImpactCard title="Unaffected" items={unaffectedList} />
      </div>
    </div>
  );
}

function SettingsPage() {
  return (
    <div className="flex flex-col">
      <div className="px-6">
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
      </div>

      <div className="px-6 pb-4">
        <MetricStrip metrics={METRICS} dividers={false} />
      </div>

      <Tabs defaultValue="providers" className="gap-0">
        <div className="border-b border-border px-6">
          <TabsList className="border-b-0">
            <TabsTrigger value="providers">Provider connections</TabsTrigger>
            <TabsTrigger value="notifications">Notifications</TabsTrigger>
            <TabsTrigger value="members">Members &amp; roles</TabsTrigger>
            <TabsTrigger value="defaults">Defaults</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="providers">
          <div className="px-6 pt-4.5 pb-5">
            <ProviderConnections />
          </div>
        </TabsContent>

        <TabsContent value="notifications">
          <div className="flex flex-col gap-4.5 px-6 pt-4.5 pb-5">
            <SectionHeader title="Notification rules" meta="5 rules · 3 channels" />
            <div className="overflow-clip rounded-lg border border-border">
              {[
                { name: "Release blocked", channel: "#phlo-ops", urgency: "Immediate" },
                { name: "Evidence degraded", channel: "#phlo-ops", urgency: "Immediate" },
                { name: "Run failed", channel: "#phlo-data", urgency: "Immediate" },
                { name: "Nightly digest", channel: "Email", urgency: "Daily 08:00" },
                { name: "Maintenance window", channel: "Email", urgency: "Advance notice" },
              ].map((rule, index) => (
                <div
                  key={rule.name}
                  className={cn(
                    "flex h-9 items-center justify-between px-3.5 text-xs",
                    index > 0 && "border-t border-border",
                  )}
                >
                  <span className="font-medium">{rule.name}</span>
                  <span className="text-muted-foreground">{rule.channel}</span>
                  <span className="text-muted-foreground">{rule.urgency}</span>
                </div>
              ))}
            </div>
          </div>
        </TabsContent>

        <TabsContent value="members">
          <div className="flex flex-col gap-4.5 px-6 pt-4.5 pb-5">
            <SectionHeader title="Members & roles" meta="14 members · 4 operators · 3 reviewers" />
            <div className="overflow-clip rounded-lg border border-border">
              {[
                { name: "Gareth Price", role: "Workspace admin", status: "Active" },
                { name: "data.steward", role: "Operator", status: "Active" },
                { name: "Policy verifier", role: "Reviewer", status: "Active" },
                { name: "Finance", role: "Viewer", status: "Invited" },
              ].map((member, index) => (
                <div
                  key={member.name}
                  className={cn(
                    "flex h-9 items-center justify-between px-3.5 text-xs",
                    index > 0 && "border-t border-border",
                  )}
                >
                  <span className="font-medium">{member.name}</span>
                  <span className="text-muted-foreground">{member.role}</span>
                  <span className={member.status === "Active" ? "text-success" : "text-warning"}>
                    {member.status}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </TabsContent>

        <TabsContent value="defaults">
          <div className="flex flex-col gap-4.5 px-6 pt-4.5 pb-5">
            <SectionHeader title="Defaults" meta="Applied across the workspace" />
            <div className="overflow-clip rounded-lg border border-border">
              {[
                { name: "Freshness target", value: "2 hours" },
                { name: "Classification default", value: "Internal" },
                { name: "Retention", value: "7 years · cold after 1" },
                { name: "Evidence retention", value: "30 days" },
                { name: "Timezone", value: "UTC" },
              ].map((row, index) => (
                <div
                  key={row.name}
                  className={cn(
                    "flex h-9 items-center justify-between px-3.5 text-xs",
                    index > 0 && "border-t border-border",
                  )}
                >
                  <span className="font-medium">{row.name}</span>
                  <span className="text-muted-foreground">{row.value}</span>
                </div>
              ))}
            </div>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export const Route = createFileRoute("/settings")({ component: SettingsPage });
