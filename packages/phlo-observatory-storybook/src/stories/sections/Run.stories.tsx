/**
 * Run detail sections: timeline, quality failure, events, traces, logs and the
 * evidence rail.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { ExecutionTimeline, KeyEvents, QualityFailure } from "@/components/sections/run/run-evidence";
import {
  ArtifactList,
  ConfigurationPanel,
  LogViewer,
  RunDetailsRail,
  TraceTable,
} from "@/components/sections/run/run-rail";
import { runConfig } from "@/data/demo";

const meta: Meta = { title: "Sections/Run" };
export default meta;

export const Timeline: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 820 }}>
      <ExecutionTimeline />
    </div>
  ),
};

export const QualityFailureDetail: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 820 }}>
      <QualityFailure />
    </div>
  ),
};

export const Events: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 620 }}>
      <KeyEvents />
    </div>
  ),
};

export const Traces: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 820 }}>
      <TraceTable />
    </div>
  ),
};

export const Logs: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 820 }}>
      <LogViewer />
    </div>
  ),
};

export const Artifacts: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 620 }}>
      <ArtifactList />
    </div>
  ),
};

export const Configuration: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 620 }}>
      <ConfigurationPanel rows={runConfig} />
    </div>
  ),
};

export const DetailsRail: StoryObj = {
  render: () => <RunDetailsRail />,
};
