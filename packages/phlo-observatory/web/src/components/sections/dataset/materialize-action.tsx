/**
 * Guarded materialization control: preview → confirm → dispatch.
 *
 * Preview calls the provider's dry-run path (`dry_run: true`) so the operator
 * sees the provider's own validation (permission, materializability, job
 * resolution) before anything is launched. Confirm dispatches the real
 * materialization under a fresh idempotency key; the launched run id in the
 * provider response becomes the navigation target. Nothing is painted as
 * successful optimistically — only what the provider reported is rendered.
 */
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Play } from "lucide-react";

import type { MaterializeResult } from "@/api/types";
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

function PreviewLine({ result }: { result: MaterializeResult }) {
  const accepted = result.accepted !== false;
  return (
    <div className="flex flex-col gap-1 rounded-[7px] border border-border px-3 py-2.5 text-[11px] leading-4">
      <span className={accepted ? "text-muted-foreground" : "text-destructive"}>
        {result.status ? `${result.status} — ` : ""}
        {result.message ?? "Provider validated the request."}
      </span>
      {result.asset_key_path ? (
        <span className="font-mono text-[10px] text-muted-foreground">
          {result.asset_key_path}
        </span>
      ) : null}
    </div>
  );
}

function CommitLine({ result }: { result: MaterializeResult }) {
  const accepted = result.accepted === true;
  return (
    <div className="flex flex-col gap-1 rounded-[7px] border border-border px-3 py-2.5 text-[11px] leading-4">
      <span className={accepted ? "text-success" : "text-destructive"}>
        {result.status ? `${result.status} — ` : ""}
        {result.message ?? (accepted ? "Materialization accepted." : "Materialization rejected.")}
      </span>
      {result.run_id ? (
        <Link
          to="/runs/$runId"
          params={{ runId: result.run_id }}
          className="text-accent-foreground underline underline-offset-2"
        >
          Open run {result.run_id}
        </Link>
      ) : null}
    </div>
  );
}

/**
 * "Materialize" header action for a dataset page. `assetId` is the dataset's
 * canonical asset key (e.g. `marts/orders`); disabled while the dataset's
 * evidence has not resolved.
 */
export function MaterializeAction({
  assetId,
  disabledReason,
}: {
  assetId: string;
  disabledReason?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [partition, setPartition] = useState("");
  const [preview, setPreview] = useState<MaterializeResult | null>(null);
  const [commit, setCommit] = useState<MaterializeResult | null>(null);
  const queryClient = useQueryClient();

  const partitionKey = partition.trim() || undefined;

  const previewMutation = useMutation({
    mutationFn: () =>
      mutations.previewMaterialize(assetId, { partitionKey }),
    onSuccess: setPreview,
  });
  const commitMutation = useMutation({
    mutationFn: () => mutations.materializeAsset(assetId, { partitionKey }),
    onSuccess: (payload) => {
      setCommit(payload);
      // A launched run changes the run list, this dataset's run refs, and
      // operations — invalidate on the confirmed result only.
      void queryClient.invalidateQueries({ queryKey: ["mission", "runs"] });
      void queryClient.invalidateQueries({ queryKey: ["mission", "datasets", assetId] });
      void queryClient.invalidateQueries({ queryKey: ["mission", "overview"] });
    },
  });

  const error = previewMutation.error ?? commitMutation.error;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setPreview(null);
          setCommit(null);
          previewMutation.reset();
          commitMutation.reset();
        }
      }}
    >
      <Button
        variant="outline"
        disabled={disabledReason != null}
        title={disabledReason ?? undefined}
        onClick={() => setOpen(true)}
      >
        <Play />
        Preview materialization
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Materialize dataset</DialogTitle>
          <DialogDescription>
            Preview asks the orchestrator to validate this materialization without launching a run.
            Confirming launches real work through the provider under a fresh idempotency key.
          </DialogDescription>
        </DialogHeader>
        <label className="flex flex-col gap-1.5 text-[11px] leading-4 text-muted-foreground">
          Partition
          <input
            value={partition}
            onChange={(event) => {
              setPartition(event.target.value);
              setPreview(null);
              setCommit(null);
              previewMutation.reset();
              commitMutation.reset();
            }}
            placeholder="Unpartitioned / provider default"
            className="rounded-[7px] border border-border bg-background px-3 py-2 font-mono text-[12px] text-foreground outline-none placeholder:text-muted-foreground"
          />
        </label>
        {preview ? <PreviewLine result={preview} /> : null}
        {commit ? <CommitLine result={commit} /> : null}
        {error ? (
          <p className="text-[11px] leading-4 text-destructive">{error.message}</p>
        ) : null}
        <DialogFooter>
          {commit ? (
            <Button variant="outline" onClick={() => setOpen(false)}>
              Close
            </Button>
          ) : preview === null ? (
            <Button disabled={previewMutation.isPending} onClick={() => previewMutation.mutate()}>
              {previewMutation.isPending ? "Validating…" : "Run preview"}
            </Button>
          ) : (
            <Button
              disabled={commitMutation.isPending || preview.accepted === false}
              title={
                preview.accepted === false
                  ? "The provider did not accept this materialization."
                  : undefined
              }
              onClick={() => commitMutation.mutate()}
            >
              {commitMutation.isPending ? "Launching…" : "Materialize"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
