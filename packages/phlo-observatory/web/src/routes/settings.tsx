/**
 * Settings route: provider connections and their blast radius.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import { queries } from "@/api/mission-control";

import { Banner } from "@/components/feedback/banner";
import { Page, PageContent, PageStack } from "@/components/layout/page";
import { PageHeader, ReadStateChip } from "@/components/layout/page-header";
import { ProviderConnections } from "@/components/sections/settings/provider-connections";

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
  const providers = useQuery(queries.providerConnections());
  const degradedProvider = providers.data?.data?.find((connection) => connection.degraded);
  const impact = useQuery({
    ...queries.providerImpact(degradedProvider?.name.toLowerCase() ?? "__none__"),
    enabled: Boolean(degradedProvider),
  });

  return (
    <Page>
      <PageHeader
        title="Settings"
        titleAccessory={<ReadStateChip evidence={providers.data?.evidence} />}
        description="Provider connections and what depends on them."
      />

      <PageContent>
        <PageStack>
          {degradedProvider ? (
            <Banner
              tone="danger"
              title={`${degradedProvider.name} is ${degradedProvider.state.toLowerCase()}`}
              detail={degradedProvider.detail}
            />
          ) : null}
          {providers.data?.data ? (
            <ProviderConnections
              connections={providers.data.data}
              title={`Connections · ${providers.data.data.length}`}
              meta="Provider-reported state"
            />
          ) : null}
          {impact.data?.data ? (
            <div className="flex gap-4">
              <ImpactCard
                title={`Degraded while ${impact.data.data.provider} is down`}
                items={impact.data.data.degraded}
              />
              <ImpactCard title="Unaffected" items={impact.data.data.unaffected} />
            </div>
          ) : null}
        </PageStack>
      </PageContent>
    </Page>
  );
}

export const Route = createFileRoute("/settings")({ component: SettingsPage });
