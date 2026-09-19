/**
 * Runs index route: the real `/runs` substrate collection — cursor-paginated
 * and polled while the page is active. Rows deep-link into `/runs/$runId`.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useInfiniteQuery } from "@tanstack/react-query";

import type { DataColumn } from "@/components/data/data-table";
import type { MissionRunRow } from "@/api/types";
import { queries } from "@/api/mission-control";
import { DataTable } from "@/components/data/data-table";
import { Identifier } from "@/components/data/identifier";
import { StatusPill } from "@/components/data/status-pill";
import { Page, PageContent } from "@/components/layout/page";
import { PageHeader } from "@/components/layout/page-header";
import { Section } from "@/components/layout/section-header";
import { Button } from "@/components/ui/button";

function elapsed(seconds: number | null): string {
  if (!seconds || seconds <= 0) return "—";
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}m ${total % 60}s`;
}

const COLUMNS: Array<DataColumn<MissionRunRow>> = [
  {
    key: "run",
    header: "Run",
    cell: (run) => (
      <span className="flex flex-col gap-0.5 py-1">
        <span className="font-semibold leading-4">{run.name}</span>
        <Identifier value={run.id} head={12} className="text-[11px] text-muted-foreground" />
      </span>
    ),
  },
  {
    key: "status",
    header: "Status",
    width: "w-32",
    cell: (run) => <StatusPill status={run.status} dot={false} />,
  },
  {
    key: "assets",
    header: "Assets",
    width: "w-24",
    cell: (run) => String(run.asset_ids.length),
    className: "text-muted-foreground",
  },
  {
    key: "started",
    header: "Started",
    cell: (run) => run.started_at ?? "—",
    className: "text-muted-foreground",
  },
  {
    key: "duration",
    header: "Duration",
    width: "w-24",
    cell: (run) => elapsed(run.duration_seconds),
    className: "text-muted-foreground",
  },
];

function RunsIndexPage() {
  const navigate = useNavigate();
  const runs = useInfiniteQuery(queries.runsList());

  const rows = runs.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <Page>
      <PageHeader
        title="Runs"
        description="Every run the configured orchestrator reports."
      />
      <PageContent>
        <Section title="All runs" meta={`${rows.length} loaded${runs.hasNextPage ? "+" : ""}`}>
          <DataTable
            columns={COLUMNS}
            rows={rows}
            rowKey={(run) => run.id}
            onRowClick={(run) => navigate({ to: "/runs/$runId", params: { runId: run.id } })}
            empty={
              runs.isError
                ? "The orchestrator could not return runs."
                : "No runs reported yet."
            }
          />
          {runs.hasNextPage ? (
            <div className="mt-3 flex justify-center">
              <Button
                variant="outline"
                onClick={() => runs.fetchNextPage()}
                disabled={runs.isFetchingNextPage}
              >
                {runs.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            </div>
          ) : null}
        </Section>
      </PageContent>
    </Page>
  );
}

export const Route = createFileRoute("/runs/")({
  component: RunsIndexPage,
});
