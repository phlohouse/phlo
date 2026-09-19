/**
 * Overview rail: service health, release queue, governance and recovery.
 *
 * Presentational except the service-health "Configure" dialog, which records
 * the operator's service selection through the mission API and refreshes the
 * rail — the rail's counts and rows always reflect the stored configuration.
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import type { MissionOverviewRail, MissionRailStat } from "@/api/types";
import { mutations } from "@/api/mission-control";
import { StatusPill } from "@/components/data/status-pill";
import { DetailRail, DetailSection } from "@/components/layout/detail-rail";
import { InlineLink } from "@/components/layout/section-header";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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

/** Effective selection: the recorded list, or the rail's default view (every long-running service). */
function defaultSelection(rail: MissionOverviewRail): Set<string> {
  const visibility = rail.service_visibility;
  if (visibility.configured && visibility.shown !== null) return new Set(visibility.shown);
  return new Set(
    rail.service_options.filter((option) => option.lifecycle !== "task").map((o) => o.name),
  );
}

function ServiceVisibilityDialog({
  rail,
  open,
  onOpenChange,
}: {
  rail: MissionOverviewRail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(() => defaultSelection(rail));
  const saveMutation = useMutation({
    mutationFn: (shown: Array<string> | null) => mutations.updateServiceVisibility(shown),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["mission", "overview-rail"] });
      onOpenChange(false);
    },
  });

  const toggle = (name: string, checked: boolean) => {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(name);
      else next.delete(name);
      return next;
    });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) setSelected(defaultSelection(rail));
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Service health</DialogTitle>
          <DialogDescription>
            Choose which catalog services appear on the Overview. One-shot setup tasks are
            hidden by default but can be included explicitly.
          </DialogDescription>
        </DialogHeader>
        <div className="flex max-h-72 flex-col overflow-y-auto rounded-[7px] border border-border">
          {rail.service_options.map((option) => (
            <label
              key={option.name}
              className="flex h-8 cursor-pointer items-center gap-2.5 border-b border-border px-3 last:border-b-0 hover:bg-muted"
            >
              <Checkbox
                checked={selected.has(option.name)}
                onCheckedChange={(checked) => toggle(option.name, checked === true)}
              />
              <span className="truncate text-xs font-semibold">{option.name}</span>
              <span className="truncate text-[11px] text-muted-foreground">{option.role}</span>
              {option.lifecycle === "task" ? (
                <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">
                  setup task
                </span>
              ) : (
                <span
                  className={cn(
                    "ml-auto shrink-0 text-[11px]",
                    option.state === "Ready" ? "text-success" : "text-warning",
                  )}
                >
                  {option.state}
                </span>
              )}
            </label>
          ))}
        </div>
        {saveMutation.error ? (
          <p className="text-[11px] text-destructive">{saveMutation.error.message}</p>
        ) : null}
        <DialogFooter>
          <Button
            variant="ghost"
            disabled={saveMutation.isPending}
            onClick={() => saveMutation.mutate(null)}
          >
            Reset to default
          </Button>
          <Button
            disabled={saveMutation.isPending}
            onClick={() =>
              saveMutation.mutate(
                rail.service_options.map((o) => o.name).filter((name) => selected.has(name)),
              )
            }
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function HealthRail({
  rail,
  onOpen,
}: {
  rail: MissionOverviewRail;
  onOpen?: {
    services?: () => void;
    service?: (name: string) => void;
    releases?: () => void;
    governance?: () => void;
    recovery?: () => void;
  };
}) {
  const {
    services,
    release_queue: releaseQueue,
    governance,
    recovery,
    ready_count: readyCount,
    total_services: totalServices,
  } = rail;
  const [configureOpen, setConfigureOpen] = useState(false);
  return (
    <DetailRail>
      <DetailSection
        title="Service health"
        meta={`${readyCount} / ${totalServices} ready`}
        action={<InlineLink onClick={() => setConfigureOpen(true)}>Configure</InlineLink>}
      >
        <div className="overflow-clip rounded-[7px] border border-border">
          {services.map((service) => (
            <button
              key={service.name}
              type="button"
              onClick={() => onOpen?.service?.(service.name)}
              className="flex h-7 w-full cursor-pointer items-center border-b border-border px-2.5 text-left last:border-b-0 hover:bg-muted"
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
            </button>
          ))}
          {services.length === 0 ? (
            <div className="flex h-7 items-center px-2.5 text-[11px] text-muted-foreground">
              No services selected
            </div>
          ) : null}
          <div className="flex h-7 items-center px-2.5 text-[11px]">
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

      <ServiceVisibilityDialog rail={rail} open={configureOpen} onOpenChange={setConfigureOpen} />
    </DetailRail>
  );
}
