/**
 * Dataset ownership rail: contract terms, downstream consumers and the
 * delivery/access summary for the selected dataset.
 */
import { ChevronRight } from "lucide-react";

import { DetailRail, DetailSection } from "@/components/layout/detail-rail";
import { InlineLink, SectionHeader } from "@/components/layout/section-header";
import { PropertyList } from "@/components/data/property-list";

export interface Consumer {
  name: string;
  role: string;
}

export function DatasetOwnershipRail({
  ownership,
  consumers,
  access,
  onViewContract,
}: {
  ownership: Array<{ label: string; value: React.ReactNode }>;
  consumers: Array<Consumer>;
  access: Array<{ label: string; value: React.ReactNode }>;
  onViewContract?: () => void;
}) {
  return (
    <DetailRail>
      <DetailSection title="Ownership & contract">
        <PropertyList rows={ownership} />
      </DetailSection>

      <DetailSection>
        <SectionHeader title="Consumers" meta={`${consumers.length} downstream`} />
        {consumers.map((consumer) => (
          <div key={consumer.name} className="flex items-center gap-2">
            <span className="flex flex-1 flex-col gap-0.75">
              <span className="text-xs font-medium">{consumer.name}</span>
              <span className="text-[10px] leading-3 text-muted-foreground">{consumer.role}</span>
            </span>
            <ChevronRight className="size-4.5 text-muted-foreground" />
          </div>
        ))}
      </DetailSection>

      <DetailSection title="Delivery & access">
        <PropertyList rows={access} divided={false} />
        <InlineLink onClick={onViewContract}>View contract & access →</InlineLink>
      </DetailSection>
    </DetailRail>
  );
}
