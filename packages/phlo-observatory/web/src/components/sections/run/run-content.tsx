/**
 * Run content region: every run tab shares the same main column plus the
 * evidence rail, so the split is owned here rather than repeated per tab.
 */
import * as React from "react";

import type { MissionRunDetail, RunArtifact, RunConsumer } from "@/api/types";
import { PageContent } from "@/components/layout/page";
import { RunDetailsRail } from "@/components/sections/run/run-rail";

export function RunContent({
  run,
  consumers,
  artifacts,
  children,
}: {
  run?: MissionRunDetail;
  consumers?: Array<RunConsumer>;
  artifacts?: Array<RunArtifact>;
  children: React.ReactNode;
}) {
  return (
    <PageContent
      rail={
        run ? (
          <RunDetailsRail run={run} consumers={consumers ?? []} artifacts={artifacts ?? []} />
        ) : null
      }
    >
      {children}
    </PageContent>
  );
}
