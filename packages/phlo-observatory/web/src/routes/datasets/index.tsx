/**
 * Dataset index route: the real `/datasets` substrate collection with
 * server-side search and cursor pagination. Rows deep-link into
 * `/datasets/$datasetId` with the provider's canonical id.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useDeferredValue } from "react";
import { parseAsString, useQueryState } from "nuqs";
import { Search } from "lucide-react";

import type { DataColumn } from "@/components/data/data-table";
import type { ObservatoryDataset } from "@/api/types";
import { queries } from "@/api/mission-control";
import { DataTable } from "@/components/data/data-table";
import { StatusPill } from "@/components/data/status-pill";
import { Page, PageBand, PageContent } from "@/components/layout/page";
import { PageHeader } from "@/components/layout/page-header";
import { Section } from "@/components/layout/section-header";
import { Button } from "@/components/ui/button";

const COLUMNS: Array<DataColumn<ObservatoryDataset>> = [
  {
    key: "name",
    header: "Dataset",
    cell: (dataset) => (
      <span className="flex flex-col gap-0.5 py-1">
        <span className="font-semibold leading-4">{dataset.name}</span>
        {dataset.id !== dataset.name ? (
          <span className="font-mono text-[11px] leading-3.5 text-muted-foreground">
            {dataset.id}
          </span>
        ) : null}
      </span>
    ),
  },
  {
    key: "owner",
    header: "Owner",
    cell: (dataset) => dataset.owner ?? "Unassigned",
    className: "text-muted-foreground",
  },
  {
    key: "state",
    header: "State",
    width: "w-32",
    cell: (dataset) => (
      <StatusPill status={dataset.candidate ? "Candidate" : dataset.publication_state} dot={false} />
    ),
  },
  {
    key: "readiness",
    header: "Readiness",
    width: "w-32",
    cell: (dataset) => dataset.readiness_state,
    className: "text-muted-foreground",
  },
];

function DatasetsIndexPage() {
  const navigate = useNavigate();
  const [input, setInput] = useQueryState("q", parseAsString.withDefault(""));
  const search = useDeferredValue(input);
  const datasets = useInfiniteQuery(queries.datasetsList(search));

  const rows = datasets.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <Page>
      <PageHeader
        title="Datasets"
        description="Every dataset the configured project declares — assets, promoted tables and candidates."
      />
      <PageBand>
        <div className="flex items-center gap-2 rounded-[7px] border border-border bg-background px-3 py-2">
          <Search className="size-4 shrink-0 text-muted-foreground" />
          <input
            value={input}
            onChange={(event) => void setInput(event.target.value)}
            placeholder="Filter datasets"
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>
      </PageBand>
      <PageContent>
        <Section
          title="All datasets"
          meta={`${rows.length} loaded${datasets.hasNextPage ? "+" : ""}`}
        >
          <DataTable
            columns={COLUMNS}
            rows={rows}
            rowKey={(dataset) => dataset.id}
            onRowClick={(dataset) =>
              navigate({ to: "/datasets/$", params: { _splat: dataset.id } })
            }
            empty={
              datasets.isError
                ? "The dataset source is unavailable."
                : "No datasets match this filter."
            }
          />
          {datasets.hasNextPage ? (
            <div className="mt-3 flex justify-center">
              <Button
                variant="outline"
                onClick={() => datasets.fetchNextPage()}
                disabled={datasets.isFetchingNextPage}
              >
                {datasets.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            </div>
          ) : null}
        </Section>
      </PageContent>
    </Page>
  );
}

export const Route = createFileRoute("/datasets/")({
  component: DatasetsIndexPage,
});
