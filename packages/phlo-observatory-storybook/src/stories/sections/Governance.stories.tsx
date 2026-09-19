/**
 * Governance sections: publication reviews, access drift, ownership gaps and
 * the audit trail.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { AccessDriftPanel } from "@/components/sections/governance/access-drift-panel";
import { AuditTable } from "@/components/sections/governance/audit-table";
import { OwnershipGapsTable } from "@/components/sections/governance/ownership-gaps-table";
import { PublicationReviewsTable } from "@/components/sections/governance/publication-reviews-table";
import { accessDriftRows, auditActivityRows, ownershipGaps, publicationReviewRows } from "../../fixtures/demo";

const meta: Meta = { title: "Sections/Governance" };
export default meta;

export const PublicationReviews: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 940 }}>
      <PublicationReviewsTable rows={publicationReviewRows} />
    </div>
  ),
};

export const AccessDrift: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 820 }}>
      <AccessDriftPanel
        title="Access drift · Orders"
        verdict="Unexpected UPDATE grant"
        subtitle="Postgres · marts.orders · Role finance_reader · Verified at 09:32 UTC"
        rows={accessDriftRows}
        note="Review the grant change before synchronizing policy."
        action="Preview grant reconciliation"
      />
    </div>
  ),
};

export const OwnershipGaps: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 820 }}>
      <OwnershipGapsTable rows={ownershipGaps} />
    </div>
  ),
};

export const Audit: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 1000 }}>
      <AuditTable rows={auditActivityRows} />
    </div>
  ),
};
