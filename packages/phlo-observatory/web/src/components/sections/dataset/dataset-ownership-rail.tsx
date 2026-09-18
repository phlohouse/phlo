/**
 * Dataset ownership rail: contract terms, downstream consumers and the
 * delivery/access summary for the selected dataset.
 */
import { ChevronRight } from "lucide-react";

import type { DatasetAccessGrant, DatasetOwnership } from "@/api/types";
import { DetailRail, DetailSection } from "@/components/layout/detail-rail";
import { InlineLink, SectionHeader } from "@/components/layout/section-header";
import { PropertyList } from "@/components/data/property-list";

export function DatasetOwnershipRail({
  ownership,
  access,
  onViewContract,
}: {
  ownership: DatasetOwnership;
  access: Array<DatasetAccessGrant>;
  onViewContract?: () => void;
}) {
  const ownershipRows = [
    { label: "Owner", value: ownership.owner ?? "Unassigned" },
    { label: "Domain", value: ownership.domain ?? "—" },
    { label: "Freshness target", value: ownership.freshness_target ?? "—" },
    { label: "Schedule", value: ownership.schedule ?? "—" },
    { label: "Classification", value: ownership.classification ?? "—" },
    { label: "Contract version", value: ownership.contract_version ?? "—" },
  ];
  const accessRows = access.map((grant) => ({ label: grant.principal, value: grant.scope }));
  return (
    <DetailRail>
      <DetailSection title="Ownership & contract">
        <PropertyList rows={ownershipRows} />
      </DetailSection>

      <DetailSection>
        <SectionHeader title="Consumers" meta={`${access.length} downstream`} />
        {access.map((grant) => (
          <div key={grant.principal} className="flex items-center gap-2">
            <span className="flex flex-1 flex-col gap-0.75">
              <span className="text-xs font-medium">{grant.principal}</span>
              <span className="text-[10px] leading-3 text-muted-foreground">{grant.scope}</span>
            </span>
            <ChevronRight className="size-4.5 text-muted-foreground" />
          </div>
        ))}
      </DetailSection>

      <DetailSection title="Delivery & access">
        <PropertyList rows={accessRows} divided={false} />
        <InlineLink onClick={onViewContract}>View contract & access →</InlineLink>
      </DetailSection>
    </DetailRail>
  );
}
