/**
 * Guarded run actions: confirm → dispatch → render the typed result.
 *
 * The API binds each generated idempotency key to the request payload and
 * rechecks target state at commit; this component renders the resulting
 * `RunActionResult.status` verbatim (accepted / pending / reconciled /
 * rejected / skipped) along with its durable verification handle.
 */
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Play, Square } from "lucide-react";

import type { RunActionResult } from "@/api/types";
import { mutations } from "@/api/mission-control";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const RETRYABLE = new Set(["FAILURE", "FAILED", "CANCELED", "CANCELLED"]);
const CANCELLABLE = new Set([
  "QUEUED",
  "NOT_STARTED",
  "STARTING",
  "STARTED",
  "MANAGED",
  "CANCELING",
]);

const RESULT_TONE: Record<string, string> = {
  accepted: "text-success",
  reconciled: "text-success",
  pending: "text-warning",
  rejected: "text-destructive",
  skipped: "text-muted-foreground",
};

function ResultLine({ result }: { result: RunActionResult }) {
  return (
    <div className="flex flex-col gap-1 rounded-[7px] border border-border px-3 py-2.5 text-[11px] leading-4">
      <span className={RESULT_TONE[result.status] ?? "text-muted-foreground"}>
        {result.status}
        {result.message ? ` — ${result.message}` : ""}
      </span>
      <span className="font-mono text-[10px] text-muted-foreground">
        {result.verification_handle}
      </span>
    </div>
  );
}

function ActionDialog({
  runId,
  action,
  title,
  description,
  confirmLabel,
  disabledReason,
  invoke,
}: {
  runId: string;
  action: "retry" | "cancel";
  title: string;
  description: string;
  confirmLabel: string;
  disabledReason: string | null;
  invoke: () => Promise<RunActionResult>;
}) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<RunActionResult | null>(null);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: invoke,
    onSuccess: (payload) => {
      setResult(payload);
      // The action's effect lands asynchronously: refresh the run's reads.
      void queryClient.invalidateQueries({ queryKey: ["mission", "runs", runId] });
      void queryClient.invalidateQueries({ queryKey: ["mission", "runs"] });
    },
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button
        variant={action === "retry" ? "default" : "outline"}
        disabled={disabledReason !== null}
        title={disabledReason ?? undefined}
        onClick={() => {
          setResult(null);
          setOpen(true);
        }}
      >
        {action === "retry" ? <Play /> : <Square />}
        {title}
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {result ? <ResultLine result={result} /> : null}
        {mutation.isError ? (
          <p className="text-[11px] leading-4 text-destructive">
            {mutation.error.message}
          </p>
        ) : null}
        <DialogFooter>
          {result === null ? (
            <Button
              disabled={mutation.isPending}
              onClick={() => mutation.mutate()}
            >
              {mutation.isPending ? "Dispatching…" : confirmLabel}
            </Button>
          ) : (
            <Button variant="outline" onClick={() => setOpen(false)}>
              Close
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Retry/cancel controls for the run header — only the action the run's
 * live status actually permits is rendered; a terminal run offers nothing. */
export function RunActions({ runId, status }: { runId: string; status: string }) {
  const normalized = status.toUpperCase();
  return (
    <>
      {RETRYABLE.has(normalized) ? (
        <ActionDialog
          runId={runId}
          action="retry"
          title="Retry run"
          description="Launches a new attempt of this run through the orchestrator. The request is idempotency-bound and re-checked against the run's live state at commit."
          confirmLabel="Retry run"
          disabledReason={null}
          invoke={() => mutations.retryRun(runId)}
        />
      ) : null}
      {CANCELLABLE.has(normalized) ? (
        <ActionDialog
          runId={runId}
          action="cancel"
          title="Cancel run"
          description="Requests termination of this run through the orchestrator. The request is idempotency-bound and re-checked against the run's live state at commit."
          confirmLabel="Cancel run"
          disabledReason={null}
          invoke={() => mutations.cancelRun(runId)}
        />
      ) : null}
    </>
  );
}
