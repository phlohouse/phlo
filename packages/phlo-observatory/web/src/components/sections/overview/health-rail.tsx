/**
 * Overview rail: service health, release queue, governance and recovery.
 *
 * Presentational — the rail receives its rows and delegates navigation.
 */
import type { MissionOverviewRail, MissionRailStat } from "@/api/types";
import { StatusPill } from "@/components/data/status-pill";
import { DetailRail, DetailSection } from "@/components/layout/detail-rail";
import { InlineLink } from "@/components/layout/section-header";
import { cn } from "@/lib/utils";

function RailList({ rows, onOpen }: { rows: Array<MissionRailStat>; onOpen?: () => void }) {
  return (
    <div className="flex flex-col gap-2.25">
      {rows.map((row) => (
        <button
          key={row.label}
          type="button"
          onClick={onOpen}
          className="flex h-4.5 shrink-0 cursor-pointer items-center justify-between gap-2.5 text-left"
        >
          <span className="text-xs text-foreground">{row.label}</span>
          <span className={cn("text-[11px] leading-3.5", row.tone)}>{row.value}</span>
        </button>
      ))}
    </div>
  );
}

export function HealthRail({
  rail,
  onOpen,
}: {
  rail: MissionOverviewRail;
  onOpen?: {
    services?: () => void;
    releases?: () => void;
    governance?: () => void;
    recovery?: () => void;
  };
}) {
  const { services, release_queue: releaseQueue, governance, recovery, ready_count: readyCount, total_services: totalServices } = rail;
  const remaining = Math.max(0, totalServices - services.length);
  return (
    <DetailRail>
      <DetailSection title="Service health" meta={`${readyCount} / ${totalServices} ready`}>
        <div className="overflow-clip rounded-[7px] border border-border">
          {services.map((service) => (
            <div
              key={service.name}
              className="flex h-7 items-center border-b border-border px-2.5 last:border-b-0"
            >
              <span className="w-24 shrink-0 truncate text-xs font-semibold">{service.name}</span>
              <span className="truncate pl-2.5 text-[11px] text-muted-foreground">
                {service.role}
              </span>
              <span
                className={cn(
                  "ml-auto flex shrink-0 items-center gap-1.5 pr-1 text-[11px]",
                  service.state === "Ready" ? "text-success" : "text-warning",
                )}
              >
                <span
                  className={cn(
                    "size-1.5 rounded-full",
                    service.state === "Ready" ? "bg-success" : "bg-warning",
                  )}
                />
                {service.state}
              </span>
            </div>
          ))}
          <div className="flex h-7 items-center px-2.5 text-[11px] text-muted-foreground">
            <span>{remaining} more services ready</span>
            <InlineLink className="ml-auto" onClick={onOpen?.services}>
              View all services
            </InlineLink>
          </div>
        </div>
      </DetailSection>

      <DetailSection title="Release queue" meta={`${releaseQueue.length} pending`}>
        <div className="overflow-clip rounded-[7px] border border-border">
          {releaseQueue.map((item) => (
            <button
              key={item.name}
              type="button"
              onClick={onOpen?.releases}
              className="flex w-full shrink-0 cursor-pointer flex-col gap-1.25 border-b border-border px-3 py-2.5 text-left last:border-b-0 hover:bg-muted"
            >
              <span className="flex items-center justify-between gap-2">
                <span className="truncate font-mono text-xs font-medium">{item.name}</span>
                <StatusPill status={item.state} />
              </span>
              <span className="truncate text-[11px] leading-3.5 text-muted-foreground">
                {item.detail}
              </span>
            </button>
          ))}
        </div>
      </DetailSection>

      <DetailSection
        title="Governance"
        action={<InlineLink onClick={onOpen?.governance}>View controls</InlineLink>}
      >
        <RailList rows={governance} onOpen={onOpen?.governance} />
      </DetailSection>

      <DetailSection
        title="Recovery & maintenance"
        action={<InlineLink onClick={onOpen?.recovery}>View operations</InlineLink>}
      >
        <RailList rows={recovery} onOpen={onOpen?.recovery} />
      </DetailSection>
    </DetailRail>
  );
}
