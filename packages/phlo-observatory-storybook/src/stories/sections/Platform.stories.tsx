/**
 * Platform sections: service health table and the degraded-service rail.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { ServiceDiagnosticRail } from "@/components/sections/platform/service-diagnostic-rail";
import { ServiceHealthTable } from "@/components/sections/platform/service-health-table";
import { dependencyPath, lokiDetail, platformServiceRows } from "../../fixtures/demo";

const meta: Meta = { title: "Sections/Platform" };
export default meta;

export const ServiceHealth: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 900 }}>
      <ServiceHealthTable rows={platformServiceRows} />
    </div>
  ),
};

export const DegradedServiceRail: StoryObj = {
  render: () => (
    <ServiceDiagnosticRail
      serviceName="Loki"
      state="Not ready"
      stateTone="text-warning"
      summary="Container is running. /ready timed out after 5 seconds; log queries also failed."
      facts={lokiDetail}
      dependencies={dependencyPath}
      dependencyNote="Collector readiness does not confirm log delivery. Check exporter retries and backend ingestion."
      capabilityChecks="Compatibility checks: 16 / 16 passed"
      capabilities={[
        { label: "Support channel", value: "Alpha", tone: "text-warning" },
        { label: "Production readiness", value: "Not certified", tone: "text-warning" },
      ]}
    />
  ),
};
