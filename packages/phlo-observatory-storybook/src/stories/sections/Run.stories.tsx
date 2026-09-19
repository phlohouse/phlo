/**
 * Run detail sections: timeline, quality report, events, traces, logs and the
 * evidence rail — all fed by MC-04-shaped run evidence fixtures.
 */
import type { Meta, StoryObj } from "@storybook/react";

import {
  ExecutionTimeline,
  KeyEvents,
  QualityReport,
} from "@/components/sections/run/run-evidence";
import {
  ArtifactList,
  ConfigurationPanel,
  LogViewer,
  RunDetailsRail,
  TraceTable,
} from "@/components/sections/run/run-rail";
import {
  missionRunDetail,
  missionRunEvents,
  missionRunLogLines,
  runArtifactRows,
  runConfigRows,
  runQualityReport,
  runSpanViews,
  runStageViews,
} from "../../fixtures/demo";

const meta: Meta = { title: "Sections/Run" };
export default meta;

export const Timeline: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 820 }}>
      <ExecutionTimeline stages={runStageViews} />
    </div>
  ),
};

export const QualityReportDetail: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 820 }}>
      <QualityReport report={runQualityReport} />
    </div>
  ),
};

export const Events: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 620 }}>
      <KeyEvents events={missionRunEvents} />
    </div>
  ),
};

export const Traces: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 820 }}>
      <TraceTable spans={runSpanViews} />
    </div>
  ),
};

export const Logs: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 820 }}>
      <LogViewer lines={missionRunLogLines} total={missionRunLogLines.length} />
    </div>
  ),
};

export const Artifacts: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 620 }}>
      <ArtifactList artifacts={runArtifactRows} />
    </div>
  ),
};

export const Configuration: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 620 }}>
      <ConfigurationPanel rows={runConfigRows} />
    </div>
  ),
};

export const DetailsRail: StoryObj = {
  render: () => (
    <RunDetailsRail
      run={missionRunDetail}
      consumers={missionRunDetail.consumers}
      artifacts={missionRunDetail.artifacts}
    />
  ),
};
