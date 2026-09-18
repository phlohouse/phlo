/**
 * Overview page section: health rail.
 */
import { useNavigate } from "@tanstack/react-router";

import { InlineLink, SectionHeader } from "@/components/section-header";
import { StatusPill } from "@/components/status-pill";
import { releaseQueue, services } from "@/data/demo";
import { cn } from "@/lib/utils";

const VISIBLE_SERVICES = 6;

const GOVERNANCE_ROWS = [
  { label: "Dataset ownership", value: "124 / 128 assigned", tone: "text-warning" },
  { label: "Access policies", value: "In sync", tone: "text-success" },
  { label: "Publication reviews", value: "2 awaiting review", tone: "text-accent-foreground" },
];

const RECOVERY_ROWS = [
  { label: "Last backup", value: "06:00 · Verified", tone: "text-success" },
  { label: "Restore rehearsal", value: "3 days ago · Passed", tone: "text-success" },
  { label: "Table maintenance", value: "2 optimizations due", tone: "text-warning" },
];

/** Bordered 7px card used by the service-health and release-queue blocks. */
function RailCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-col overflow-clip rounded-[7px] border border-border bg-card">
      {children}
    </div>
  );
}

/** Borderless label/value list used by the governance and recovery blocks. */
function RailList({ rows }: { rows: { label: string; value: string; tone: string }[] }) {
  const navigate = useNavigate();
  const target = rows[0]?.label === "Last backup" ? "/platform" : "/governance";
  return (
    <div className="flex flex-col gap-2.25">
      {rows.map((row) => (
        <button
          key={row.label}
          type="button"
          onClick={() => navigate({ to: target })}
          className="flex h-4.5 shrink-0 cursor-pointer items-center justify-between gap-2.5 text-left"
        >
          <span className="text-xs text-foreground">{row.label}</span>
          <span className={cn("text-[11px] leading-3.5", row.tone)}>{row.value}</span>
        </button>
      ))}
    </div>
  );
}

/** Right-hand rail: service health, release queue, governance, recovery. */
export function HealthRail() {
  const navigate = useNavigate();
  const readyCount = services.filter((service) => service.state === "Ready").length;
  const visible = services.slice(0, VISIBLE_SERVICES);

  return (
    <div className="flex w-[350px] shrink-0 flex-col gap-4">
      <section className="flex flex-col">
        <SectionHeader title="Service health" meta={`${readyCount} / ${services.length} ready`} />
        <RailCard>
          {visible.map((service) => (
            <div
              key={service.name}
              className="flex h-7 shrink-0 items-center border-b border-border px-2.5"
            >
              <span className="w-24 shrink-0 truncate text-xs font-semibold text-foreground">
                {service.name}
              </span>
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
          <div className="flex h-7 shrink-0 items-center px-2.5 text-[11px] text-muted-foreground">
            <span>{services.length - VISIBLE_SERVICES} more services ready</span>
            <InlineLink className="ml-auto" onClick={() => navigate({ to: "/platform" })}>
              View all services
            </InlineLink>
          </div>
        </RailCard>
      </section>

      <section className="flex flex-col">
        <SectionHeader title="Release queue" meta="2 pending" />
        <RailCard>
          {releaseQueue.map((item) => (
            <button
              key={item.name}
              type="button"
              onClick={() => navigate({ to: "/releases" })}
              className="flex shrink-0 cursor-pointer flex-col gap-1.25 border-b border-border px-3 py-2.5 text-left last:border-b-0 hover:bg-muted"
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
        </RailCard>
      </section>

      <section className="flex flex-col">
        <SectionHeader
          title="Governance"
          action={
            <InlineLink onClick={() => navigate({ to: "/governance" })}>View controls</InlineLink>
          }
        />
        <RailList rows={GOVERNANCE_ROWS} />
      </section>

      <section className="flex flex-col">
        <SectionHeader
          title="Recovery & maintenance"
          action={
            <InlineLink onClick={() => navigate({ to: "/platform" })}>View operations</InlineLink>
          }
        />
        <RailList rows={RECOVERY_ROWS} />
      </section>
    </div>
  );
}
