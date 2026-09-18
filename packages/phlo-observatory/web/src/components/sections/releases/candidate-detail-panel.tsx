/**
 * Selected release candidate: snapshot changes, required evidence and the
 * guarded publication plan.
 */
import type { CandidateSnapshotChange, ReleaseCandidateDetail } from "@/api/types";
import type {DataColumn} from "@/components/data/data-table";
import {  DataTable } from "@/components/data/data-table";
import { EvidenceList, EvidenceListPlain } from "@/components/data/evidence-list";
import { StatusPill } from "@/components/data/status-pill";
import { DetailRail, DetailSection } from "@/components/layout/detail-rail";
import { InlineLink, Section } from "@/components/layout/section-header";
import { Button } from "@/components/ui/button";

const CHANGE_COLUMNS: Array<DataColumn<CandidateSnapshotChange>> = [
  {
    key: "table",
    header: "Table",
    cell: (row) => <span className="font-medium">{row.table}</span>,
  },
  { key: "released", header: "Released snapshot", width: "w-42.5", cell: (row) => row.released_snapshot },
  {
    key: "candidate",
    header: "Audited candidate",
    width: "w-42.5",
    cell: (row) => row.candidate_snapshot,
  },
  {
    key: "delta",
    header: "Row change",
    width: "w-22.5",
    align: "right",
    cell: (row) => row.row_delta,
  },
];

export function CandidateDetailPanel({
  candidate,
  onOpenDataset,
}: {
  candidate: ReleaseCandidateDetail;
  onOpenDataset?: () => void;
}) {
  return (
    <div className="flex gap-5.5">
      <div className="flex min-w-0 flex-1 flex-col gap-4.5">
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-1.25">
            <h2 className="font-display text-[17px] leading-5.5 font-semibold">
              {candidate.id}
            </h2>
            <span className="text-[11px] leading-3.5 text-muted-foreground">
              {candidate.subtitle}
            </span>
          </div>
          <StatusPill status={candidate.status} />
        </div>

        <Section title="Snapshot changes" meta={candidate.revision}>
          <DataTable
            columns={CHANGE_COLUMNS}
            rows={candidate.snapshot_changes}
            rowKey={(row) => row.table}
          />
        </Section>

        <Section title="Required evidence" meta="Evaluated at 09:33 UTC">
          <EvidenceList
            rows={candidate.required_evidence.map((row) => ({
              name: row.name,
              detail: row.detail,
              outcome: row.outcome,
              tone: "text-success",
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
          <p className="text-[11px] leading-4 text-muted-foreground">
            Preview resolves current permissions and rechecks revision 42 before any publication.
          </p>
          <Button className="w-full">Preview publication</Button>
        </DetailSection>
        <DetailSection title="Consumer read contract" divided>
          <p className="text-xs leading-4.5">
            Consistent reads across both tables must resolve snapshots through the release record.
          </p>
          <p className="text-[11px] leading-4 text-muted-foreground">
            Reading each table's latest snapshot does not provide a consistent multi-table view.
          </p>
          <InlineLink>Inspect provider guarantees</InlineLink>
        </DetailSection>
      </DetailRail>
    </div>
  );
}
