/**
 * Run content region: every run tab shares the same main column plus the
 * evidence rail, so the split is owned here rather than repeated per tab.
 */
import * as React from "react";

import { PageContent } from "@/components/layout/page";
import { RunDetailsRail } from "@/components/sections/run/run-rail";

export function RunContent({ children }: { children: React.ReactNode }) {
  return <PageContent rail={<RunDetailsRail />}>{children}</PageContent>;
}
