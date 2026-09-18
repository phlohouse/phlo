/**
 * Release sections: pending candidates, completed releases and the candidate
 * review panel.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { CandidateDetailPanel } from "@/components/sections/releases/candidate-detail-panel";
import { CompletedReleasesTable } from "@/components/sections/releases/completed-releases-table";
import { PendingCandidatesTable } from "@/components/sections/releases/pending-candidates-table";
import { candidateDetail, latestReleases, pendingCandidates } from "@/data/demo";

const meta: Meta = { title: "Sections/Releases" };
export default meta;

export const PendingCandidates: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 980 }}>
      <PendingCandidatesTable rows={pendingCandidates} selectedId="rel-c204" />
    </div>
  ),
};

export const CompletedReleases: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 980 }}>
      <CompletedReleasesTable rows={latestReleases} />
    </div>
  ),
};

export const CandidateReview: StoryObj = {
  render: () => <CandidateDetailPanel candidate={candidateDetail} />,
};
