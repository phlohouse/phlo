/**
 * Platform diagnostic rail for a degraded service: probe history, dependency
 * path and installed capabilities.
 */
import { DetailRail, DetailSection } from "@/components/layout/detail-rail";
import { EvidenceListPlain } from "@/components/data/evidence-list";
import { PropertyList } from "@/components/data/property-list";
import { InlineLink } from "@/components/layout/section-header";
import { Button } from "@/components/ui/button";

export interface DependencyEdge {
  name: string;
  outcome: string;
  tone: string;
}

export function ServiceDiagnosticRail({
  serviceName,
  state,
  stateTone,
  summary,
  facts,
  dependencies,
  dependencyNote,
  capabilityChecks,
  capabilities,
  onInspectLogs,
  onProbeDetails,
}: {
  serviceName: string;
  state: string;
  stateTone: string;
  summary: string;
  facts: Array<{ label: string; value: React.ReactNode }>;
  dependencies: Array<DependencyEdge>;
  dependencyNote: string;
  capabilityChecks: string;
  capabilities: Array<{ label: React.ReactNode; value: React.ReactNode; tone?: string }>;
  onInspectLogs?: () => void;
  onProbeDetails?: () => void;
}) {
  return (
    <DetailRail>
      <DetailSection
        title={serviceName}
        action={<span className={`text-[11px] ${stateTone}`}>{state}</span>}
      >
        <p className="text-[11px] leading-4 text-muted-foreground">{summary}</p>
        <PropertyList rows={facts} divided={false} />
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onInspectLogs}>
            Inspect container logs
          </Button>
          <Button variant="outline" size="sm" onClick={onProbeDetails}>
            Probe details
          </Button>
        </div>
      </DetailSection>

      <DetailSection title="Dependency path" divided>
        <EvidenceListPlain
          rows={dependencies.map((edge) => ({
            label: edge.name,
            value: edge.outcome,
            tone: edge.tone,
          }))}
        />
        <p className="text-[11px] leading-4 text-muted-foreground">{dependencyNote}</p>
      </DetailSection>

      <DetailSection title="Installed capabilities" divided>
        <p className="text-xs leading-4.5">{capabilityChecks}</p>
        <p className="text-[11px] leading-4 text-muted-foreground">
          Installed compatibility is separate from declared support and deployment readiness.
        </p>
        <EvidenceListPlain rows={capabilities} />
        <InlineLink>View readiness findings</InlineLink>
      </DetailSection>
    </DetailRail>
  );
}
