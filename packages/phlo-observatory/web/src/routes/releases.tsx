/**
 * Releases route: promotion control, candidate detail, publication plan and release history.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";

import type {Metric} from "@/components/metric-strip";
import {  MetricStrip } from "@/components/metric-strip";
import { ExampleDataChip, PageHeader } from "@/components/page-header";
import { PropertyList } from "@/components/property-list";
import { InlineLink, SectionHeader } from "@/components/section-header";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  candidateDetail,
  latestReleases,
  pendingCandidates,
  releaseMetrics,
} from "@/data/demo";

const METRICS: Array<Metric> = releaseMetrics;

function readinessTone(readiness: string) {
  if (/ready/i.test(readiness)) return "text-success";
  if (/blocked/i.test(readiness)) return "text-destructive";
  return "text-foreground";
}

/** Queue of candidates awaiting promotion, one row per catalog provider. */
function PendingCandidates() {
  return (
    <section className="flex flex-col gap-2.5">
      <SectionHeader title="Pending candidates" meta="All providers · Sorted by newest" />
      <div className="overflow-clip rounded-[7px] border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-65">Candidate / dataset</TableHead>
              <TableHead className="w-60">Provider · strategy</TableHead>
              <TableHead className="w-47.5">Readiness</TableHead>
              <TableHead>Evidence</TableHead>
              <TableHead className="w-16.25">Created</TableHead>
              <TableHead className="w-15 text-right" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {pendingCandidates.map((candidate) => (
              <TableRow key={candidate.id} selected={candidate.selected}>
                <TableCell className="font-medium">
                  {candidate.id} · {candidate.dataset}
                </TableCell>
                <TableCell>{candidate.provider}</TableCell>
                <TableCell className={readinessTone(candidate.readiness)}>
                  {candidate.readiness}
                </TableCell>
                <TableCell>{candidate.evidence}</TableCell>
                <TableCell>{candidate.created}</TableCell>
                <TableCell className="text-right text-accent-foreground">
                  {candidate.action}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

/** Selected-candidate detail: snapshot diff, evidence, publication plan. */
function CandidateDetail() {
  const navigate = useNavigate();
  return (
    <div className="flex gap-5.5">
      <div className="flex min-w-0 flex-1 flex-col gap-4.5">
        <div className="flex items-start justify-between gap-4">
          <div className="flex flex-col gap-1.25">
            <h2 className="font-display text-[17px] leading-5.5 font-semibold">
              {candidateDetail.dataset} · {candidateDetail.id}
            </h2>
            <span className="text-[11px] leading-3.5 text-muted-foreground">
              {candidateDetail.subtitle}
            </span>
          </div>
          <span className="rounded-[4px] bg-success-soft px-1.75 py-1.25 text-[11px] leading-3.5 text-success">
            {candidateDetail.status}
          </span>
        </div>

        <section className="flex flex-col gap-2.5">
          <SectionHeader title="Snapshot changes" meta={candidateDetail.revision} />
          <div className="overflow-clip rounded-[7px] border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Table</TableHead>
                  <TableHead className="w-42.5">Released snapshot</TableHead>
                  <TableHead className="w-42.5">Audited candidate</TableHead>
                  <TableHead className="w-22.5 text-right">Row change</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {candidateDetail.snapshotChanges.map((change) => (
                  <TableRow key={change.table}>
                    <TableCell className="font-medium">{change.table}</TableCell>
                    <TableCell>{change.released}</TableCell>
                    <TableCell>{change.candidate}</TableCell>
                    <TableCell className="text-right">{change.delta}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </section>

        <section className="flex flex-col gap-2.5">
          <SectionHeader title="Required evidence" meta="Evaluated at 09:33 UTC" />
          <div className="overflow-clip rounded-[7px] border border-border">
            {candidateDetail.requiredEvidence.map((row) => (
              <div
                key={row.name}
                className="flex min-h-7.5 items-center border-t border-border/60 px-3 first:border-t-0"
              >
                <span className="w-40 shrink-0 text-xs font-medium">{row.name}</span>
                <span className="flex-1 text-[11px] leading-3.5 text-muted-foreground">
                  {row.detail}
                </span>
                <span className="w-17.5 shrink-0 text-right text-xs text-success">
                  {row.outcome}
                </span>
              </div>
            ))}
          </div>
        </section>

        <div className="flex items-center justify-between gap-4 rounded-md border border-border bg-subtle px-3 py-2.5">
          <span className="text-[11px] leading-3.5">
            Data release only · Target delivery and Dataset publication are tracked separately.
          </span>
          <InlineLink onClick={() => navigate({ to: "/datasets/orders" })}>Open dataset</InlineLink>
        </div>
      </div>

      <aside className="flex w-75 shrink-0 flex-col gap-5 border-l border-border pl-4.5">
        <section className="flex flex-col gap-2.5">
          <h2 className="font-display text-[15px] leading-4.5 font-semibold">Publication plan</h2>
          <PropertyList rows={candidateDetail.publicationPlan} divided={false} />
          <p className="text-[11px] leading-4 text-muted-foreground">
            Preview resolves current permissions and rechecks revision 42 before any publication.
          </p>
          <Button className="w-full">Preview publication</Button>
        </section>
        <section className="flex flex-col gap-2.25">
          <h2 className="font-display text-[15px] leading-4.5 font-semibold">
            Consumer read contract
          </h2>
          <p className="text-xs leading-4.5">
            Consistent reads across both tables must resolve snapshots through the release record.
          </p>
          <p className="text-[11px] leading-4 text-muted-foreground">
            Reading each table's latest snapshot does not provide a consistent multi-table view.
          </p>
          <InlineLink>Inspect provider guarantees</InlineLink>
        </section>
      </aside>
    </div>
  );
}

/** Recently confirmed merges, newest first. */
function LatestReleases() {
  return (
    <section className="flex flex-col gap-2.25">
      <SectionHeader title="Latest completed releases" meta="2 of 18 in the last 24 hours" />
      <div className="overflow-clip rounded-[7px] border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-65">Release</TableHead>
              <TableHead className="w-60">Provider · strategy</TableHead>
              <TableHead>Reference</TableHead>
              <TableHead className="w-32.5">Finished</TableHead>
              <TableHead className="w-32.5 text-right">Outcome</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {latestReleases.map((release) => (
              <TableRow key={release.id}>
                <TableCell className="font-medium">
                  {release.id} · {release.dataset}
                </TableCell>
                <TableCell className="text-muted-foreground">{release.provider}</TableCell>
                <TableCell>{release.ref}</TableCell>
                <TableCell className="text-muted-foreground">{release.time}</TableCell>
                <TableCell className="text-right text-success">{release.outcome}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

function ReleasesPage() {
  return (
    <div className="flex flex-col">
      <div className="px-6">
        <PageHeader
          title="Releases"
          titleAccessory={<ExampleDataChip />}
          description="Review candidate data, inspect evidence, and track what reaches consumers."
          actions={
            <>
              <Button variant="outline">Last 24 hours</Button>
              <Button>View release history</Button>
            </>
          }
        />
      </div>

      <div className="px-6 pb-4">
        <MetricStrip metrics={METRICS} dividers={false} />
      </div>

      <Tabs defaultValue="pending" className="gap-0">
        <div className="border-b border-border px-6">
          <TabsList className="border-b-0">
            <TabsTrigger value="pending">Pending · 2</TabsTrigger>
            <TabsTrigger value="history">History</TabsTrigger>
            <TabsTrigger value="operations">Operations</TabsTrigger>
            <TabsTrigger value="providers">Provider capabilities</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="pending">
          <div className="flex flex-col gap-5 px-6 pt-4.5 pb-5">
            <PendingCandidates />
            <CandidateDetail />
            <LatestReleases />
          </div>
        </TabsContent>

        <TabsContent value="history">
          <div className="px-6 pt-4.5 pb-5">
            <LatestReleases />
          </div>
        </TabsContent>

        <TabsContent value="operations">
          <div className="px-6 pt-4.5 pb-5">
            <SectionHeader title="Operations" meta="18 in the last 24 hours" />
            <LatestReleases />
          </div>
        </TabsContent>

        <TabsContent value="providers">
          <div className="flex flex-col gap-2.5 px-6 pt-4.5 pb-5">
            <SectionHeader title="Provider capabilities" meta="Polaris · Nessie" />
            <PropertyList
              rows={[
                { label: "Polaris", value: "Snapshot publication · audited" },
                { label: "Nessie", value: "Branch merge · revision checked" },
                { label: "Evidence gate", value: "Blocking" },
                { label: "Reconcile", value: "Manual" },
              ]}
            />
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export const Route = createFileRoute("/releases")({ component: ReleasesPage });
