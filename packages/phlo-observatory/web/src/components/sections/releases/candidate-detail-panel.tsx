/**
 * Selected release candidate: governed WAP evidence, snapshot changes, and
 * the read-only promotion preview.
 *
 * Identity and evidence come from the durable WAP lifecycle report — logical
 * and orchestrator run ids, staging ref, audited revisions — never from the
 * branch name alone. "Run preview" re-evaluates the promotion gates against
 * current catalog revisions; the returned digest binds every input so a stale
 * preview cannot be replayed as a valid confirmation.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { CandidateSnapshotChange, PromotionResult, ReleaseCandidateDetail } from "@/api/types";
import type {DataColumn} from "@/components/data/data-table";
import { mutations, queries } from "@/api/mission-control";
import {  DataTable } from "@/components/data/data-table";
import { Identifier } from "@/components/data/identifier";
import { EvidenceList, EvidenceListPlain } from "@/components/data/evidence-list";
import { StatusPill } from "@/components/data/status-pill";
import { DetailRail, DetailSection } from "@/components/layout/detail-rail";
import { InlineLink, Section } from "@/components/layout/section-header";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const CHANGE_COLUMNS: Array<DataColumn<CandidateSnapshotChange>> = [
  {
    key: "table",
    header: "Table",
    cell: (row) => <span className="font-medium">{row.table}</span>,
  },
  {
    key: "released",
    header: "Released snapshot",
    width: "w-32",
    cell: (row) => <Identifier value={row.released_snapshot} head={12} className="text-muted-foreground" />,
  },
  {
    key: "candidate",
    header: "Audited candidate",
    width: "w-32",
    cell: (row) => <Identifier value={row.candidate_snapshot} head={12} className="text-muted-foreground" />,
  },
  {
    key: "delta",
    header: "Row change",
    width: "w-22.5",
    align: "right",
    cell: (row) => row.row_delta,
  },
];

const CHECK_TONE: Record<string, string> = {
  passed: "text-success",
  failed: "text-destructive",
  unavailable: "text-warning",
};

const EVIDENCE_TONE: Record<string, string> = {
  success: "text-success",
  warning: "text-warning",
  danger: "text-destructive",
  muted: "text-muted-foreground",
};

const PROMOTION_OUTCOME_TONE: Record<string, string> = {
  promoted: "text-success",
  already_promoted: "text-success",
  blocked: "text-destructive",
  stale_preview: "text-warning",
  conflict: "text-destructive",
  merge_failed: "text-destructive",
  cleanup_pending: "text-warning",
  reconcile_pending: "text-warning",
  recovery_required: "text-warning",
  verification_pending: "text-warning",
  capability_unavailable: "text-destructive",
};

function PromotionResultBlock({ result }: { result: PromotionResult }) {
  return (
    <div className="flex flex-col gap-1 rounded-[7px] border border-border px-3 py-2.5 text-[11px] leading-4">
      <span className={PROMOTION_OUTCOME_TONE[result.outcome] ?? "text-muted-foreground"}>
        {result.outcome.replaceAll("_", " ")}
        {result.resumed && result.outcome === "already_promoted" ? " — reconciled" : ""}
      </span>
      {result.target_revision_after ? (
        <span className="inline-flex items-center gap-1 font-mono text-[10px] text-muted-foreground">
          released at <Identifier value={result.target_revision_after} head={16} />
        </span>
      ) : null}
      {result.blockers.map((blocker) => (
        <span key={blocker} className="text-destructive">
          {blocker}
        </span>
      ))}
      {result.failure_detail ? (
        <span className="font-mono text-[10px] leading-3.5 text-muted-foreground">
          {result.failure_detail}
        </span>
      ) : null}
    </div>
  );
}

function PromoteCandidateDialog({
  candidateId,
  previewDigest,
}: {
  candidateId: string;
  previewDigest: string;
}) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<PromotionResult | null>(null);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => mutations.promoteCandidate(candidateId, previewDigest),
    onSuccess: (payload) => {
      setResult(payload);
      // The durable report changed: refresh every release read model —
      // summary, candidates, this detail, the preview and completed releases
      // all share the "mission/releases" key prefix.
      void queryClient.invalidateQueries({ queryKey: ["mission", "releases"] });
    },
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button
        variant="outline"
        className="w-full"
        onClick={() => {
          setResult(null);
          setOpen(true);
        }}
      >
        Confirm promotion
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            Promote <Identifier value={candidateId} head={12} />
          </DialogTitle>
          <DialogDescription>
            Publish the audited candidate snapshots to the consumer-facing release.
            The server re-verifies every gate and revision before mutating.
          </DialogDescription>
        </DialogHeader>
        {result ? <PromotionResultBlock result={result} /> : null}
        {mutation.isError ? (
          <p className="text-[11px] leading-4 text-destructive">
            {mutation.error.message}
          </p>
        ) : null}
        <DialogFooter>
          {result === null ? (
            <Button disabled={mutation.isPending} onClick={() => mutation.mutate()}>
              {mutation.isPending ? "Promoting…" : "Promote"}
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

export function CandidateDetailPanel({
  candidate,
  onOpenDataset,
}: {
  candidate: ReleaseCandidateDetail;
  onOpenDataset?: () => void;
}) {
  const [previewRequested, setPreviewRequested] = useState(false);
  const preview = useQuery({
    ...queries.promotionPreview(candidate.id),
    enabled: previewRequested,
  });

  return (
    <div className="flex gap-5.5">
      <div className="flex min-w-0 flex-1 flex-col gap-4.5">
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-1.25">
            <h2 className="font-display text-[17px] leading-5.5 font-semibold">
              <Identifier value={candidate.id} head={16} />
            </h2>
            <span className="text-[11px] leading-3.5 text-muted-foreground">
              {candidate.subtitle}
            </span>
            <span className="inline-flex flex-wrap items-center gap-x-1.5 font-mono text-[10px] leading-3.5 text-muted-foreground">
              {candidate.run_id ? (
                <>
                  run <Identifier value={candidate.run_id} head={10} />
                </>
              ) : null}
              {candidate.orchestrator_run_id ? (
                <>
                  · orchestrator <Identifier value={candidate.orchestrator_run_id} head={10} />
                </>
              ) : null}
              {candidate.staging_ref ? (
                <>
                  · <Identifier value={candidate.staging_ref} head={16} />
                </>
              ) : null}
            </span>
          </div>
          <StatusPill status={candidate.status} />
        </div>

        {candidate.blockers && candidate.blockers.length > 0 ? (
          <div className="flex flex-col gap-1 rounded-[7px] border border-destructive/40 bg-destructive/5 px-3 py-2.5">
            {candidate.blockers.map((blocker) => (
              <span key={blocker} className="text-[11px] leading-4 text-destructive">
                {blocker}
              </span>
            ))}
            {candidate.failure_detail ? (
              <span className="font-mono text-[10px] leading-3.5 text-muted-foreground">
                {candidate.failure_detail}
              </span>
            ) : null}
          </div>
        ) : null}

        <Section
          title="Snapshot changes"
          meta={
            candidate.revision ? (
              <Identifier value={candidate.revision} head={16} />
            ) : undefined
          }
        >
          <DataTable
            columns={CHANGE_COLUMNS}
            rows={candidate.snapshot_changes}
            rowKey={(row) => row.table}
          />
        </Section>

        <Section title="Required evidence">
          <EvidenceList
            rows={candidate.required_evidence.map((row) => ({
              name: row.name,
              detail: row.detail,
              outcome: row.outcome,
              tone: EVIDENCE_TONE[row.tone] ?? "text-muted-foreground",
            }))}
          />
        </Section>

        <div className="flex items-center justify-between gap-4 rounded-md border border-border bg-subtle px-3 py-2.5">
          <span className="text-[11px] leading-3.5">
            Data release only · Target delivery and Dataset publication are tracked separately.
          </span>
          <InlineLink onClick={onOpenDataset}>Open dataset</InlineLink>
        </div>
      </div>

      <DetailRail>
        <DetailSection title="Publication plan">
          <EvidenceListPlain rows={candidate.publication_plan} />
          {preview.data?.data ? (
            <div className="flex flex-col gap-1.5">
              {preview.data.data.checks.map((check) => (
                <div key={check.name} className="flex items-start justify-between gap-3 text-[11px] leading-4">
                  <span className="font-medium">{check.name}</span>
                  <span className={CHECK_TONE[check.outcome] ?? "text-muted-foreground"}>
                    {check.outcome}
                  </span>
                </div>
              ))}
              <span className="inline-flex items-center gap-1 font-mono text-[10px] leading-3.5 text-muted-foreground">
                preview digest <Identifier value={preview.data.data.digest} head={16} /> ·{" "}
                {preview.data.data.eligible ? "all gates pass" : "not eligible"}
              </span>
              {preview.data.data.eligible ? (
                <PromoteCandidateDialog
                  candidateId={candidate.id}
                  previewDigest={preview.data.data.digest}
                />
              ) : null}
            </div>
          ) : preview.isError ? (
            <p className="text-[11px] leading-4 text-destructive">
              Preview unavailable — {(preview.error).message}
            </p>
          ) : (
            <p className="text-[11px] leading-4 text-muted-foreground">
              The preview re-checks the launch binding, audit decision and current
              revisions before anything may be promoted.
            </p>
          )}
          <Button
            className="w-full"
            disabled={preview.isFetching}
            onClick={() => {
              if (previewRequested) {
                void preview.refetch();
              } else {
                setPreviewRequested(true);
              }
            }}
          >
            {preview.isFetching ? "Evaluating…" : previewRequested ? "Re-run preview" : "Preview publication"}
          </Button>
        </DetailSection>
        <DetailSection title="Consumer read contract" divided>
          <p className="text-xs leading-4.5">
            Reads across the {candidate.snapshot_changes.length}{" "}
            {candidate.snapshot_changes.length === 1 ? "table" : "tables"} in this release must
            resolve snapshots through the release record.
          </p>
          <p className="text-[11px] leading-4 text-muted-foreground">
            Reading each table's latest snapshot does not provide a consistent multi-table view.
          </p>
        </DetailSection>
      </DetailRail>
    </div>
  );
}
