/**
 * Guarded service controls: probe a declared managed service, then act.
 *
 * The probe is a live provider read — runtime/readiness from the container
 * runtime, declared dependencies, and the dependents whose workflows break if
 * the service restarts. Actions come from the probe response itself: the UI
 * only offers what the server declared enabled, and every dispatch goes
 * through `/actions` under a fresh idempotency key. The result status is the
 * server's, not an optimistic paint.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";

import type { ObservatoryServiceDetail } from "@/api/types";
import { mutations, queries } from "@/api/mission-control";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const ACTION_RESULT_TONE: Record<string, string> = {
  succeeded: "text-success",
  accepted: "text-success",
  running: "text-warning",
  failed: "text-destructive",
  unknown: "text-warning",
  skipped: "text-muted-foreground",
};

function ServiceList({ label, services }: { label: string; services: Array<{ id: string; name: string; status: string }> }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      {services.length === 0 ? (
        <span className="text-[11px] text-muted-foreground">None declared</span>
      ) : (
        services.map((service) => (
          <span key={service.id} className="text-[11px] leading-4">
            {service.name} <span className="text-muted-foreground">· {service.status}</span>
          </span>
        ))
      )}
    </div>
  );
}

/**
 * "Manage" control on a service row: opens a live probe of the declared
 * service and exposes only the guarded actions the server says are enabled.
 */
export function ServiceControls({ serviceId, serviceName }: { serviceId: string; serviceName: string }) {
  const [open, setOpen] = useState(false);
  const probe = useQuery({ ...queries.serviceProbe(serviceId), enabled: open });
  const queryClient = useQueryClient();
  const [actionResult, setActionResult] = useState<{ label: string; status: string; message: string } | null>(null);

  const actionMutation = useMutation({
    mutationFn: (action: "start" | "stop" | "restart" | "add") =>
      mutations.serviceAction(serviceId, action),
    onSuccess: (result) => {
      setActionResult({
        label: result.action.label,
        status: result.status,
        message: result.message,
      });
      // The action's effect lands asynchronously in the container runtime —
      // refresh the service surfaces so they reflect actual state.
      void queryClient.invalidateQueries({ queryKey: ["mission", "platform"] });
      void queryClient.invalidateQueries({ queryKey: ["substrate", "services", serviceId] });
    },
  });

  const detail: ObservatoryServiceDetail | undefined = probe.data;
  const error = probe.error ?? actionMutation.error;
  const enabledActions = (detail?.actions ?? []).filter((action) => action.enabled);
  const disabledActions = (detail?.actions ?? []).filter((action) => !action.enabled);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) setActionResult(null);
      }}
    >
      <Button
        variant="ghost"
        size="sm"
        onClick={(event) => {
          event.stopPropagation();
          setOpen(true);
        }}
      >
        Manage
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{serviceName}</DialogTitle>
          <DialogDescription>
            Live probe of the declared service. Actions are dispatched through the guarded
            operations path; only enabled capabilities are offered.
          </DialogDescription>
        </DialogHeader>
        {probe.isPending ? (
          <p className="text-[11px] text-muted-foreground">Probing…</p>
        ) : detail ? (
          <div className="flex flex-col gap-3">
            <div className="flex gap-6 rounded-[7px] border border-border px-3 py-2.5 text-[11px] leading-4">
              <span>
                Runtime <span className="font-medium">{detail.service.runtime_state}</span>
              </span>
              <span>
                Status <span className="font-medium">{detail.service.status}</span>
              </span>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <ServiceList label="Depends on" services={detail.dependencies} />
              <ServiceList label="Dependent workflows" services={detail.dependents} />
            </div>
            {(detail.actions.find((a) => a.kind === "service.restart")?.expected_evidence ?? [])
              .length > 0 ? (
              <div className="flex flex-col gap-1">
                <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Recovery expectations
                </span>
                {(detail.actions.find((a) => a.kind === "service.restart")?.expected_evidence ?? []).map(
                  (item) => (
                    <span key={item} className="text-[11px] leading-4 text-muted-foreground">
                      {item}
                    </span>
                  ),
                )}
              </div>
            ) : null}
            {actionResult ? (
              <div className="rounded-[7px] border border-border px-3 py-2.5 text-[11px] leading-4">
                <span className={ACTION_RESULT_TONE[actionResult.status] ?? "text-muted-foreground"}>
                  {actionResult.label}: {actionResult.status}
                  {actionResult.message ? ` — ${actionResult.message}` : ""}
                </span>
              </div>
            ) : null}
          </div>
        ) : null}
        {error ? (
          <p className="text-[11px] leading-4 text-destructive">{error.message}</p>
        ) : null}
        <DialogFooter>
          {enabledActions.map((action) => (
            <Button
              key={action.id}
              variant={action.id.endsWith(":restart") ? "default" : "outline"}
              disabled={actionMutation.isPending}
              title={action.reason ?? undefined}
              onClick={() => {
                const name = action.id.split(":").pop() ?? "";
                actionMutation.mutate(name as "start" | "stop" | "restart" | "add");
              }}
            >
              {action.id.endsWith(":restart") ? <RotateCcw /> : null}
              {actionMutation.isPending ? "Dispatching…" : action.label}
            </Button>
          ))}
          {enabledActions.length === 0 && !probe.isPending ? (
            <span className="text-[11px] text-muted-foreground">
              {disabledActions[0]?.reason ?? "No actions are enabled for this service."}
            </span>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
