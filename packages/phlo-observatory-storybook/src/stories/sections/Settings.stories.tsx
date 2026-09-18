/**
 * Settings sections: provider connections and the settings table.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { ProviderConnections } from "@/components/sections/settings/provider-connections";
import { SettingsTable } from "@/components/sections/settings/settings-table";
import { providerConnections } from "@/data/demo";

const meta: Meta = { title: "Sections/Settings" };
export default meta;

export const Connections: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 940 }}>
      <ProviderConnections
        connections={providerConnections}
        title="Connections · 3"
        meta="Checks run every 60s · outcomes recorded in Audit"
      />
    </div>
  ),
};

export const NotificationRules: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 640 }}>
      <SettingsTable
        rows={[
          { name: "Release blocked", value: "#phlo-ops", meta: "Immediate" },
          { name: "Evidence degraded", value: "#phlo-ops", meta: "Immediate" },
          { name: "Run failed", value: "#phlo-data", meta: "Immediate" },
          { name: "Nightly digest", value: "Email", meta: "Daily 08:00" },
        ]}
      />
    </div>
  ),
};

export const Members: StoryObj = {
  render: () => (
    <div style={{ maxWidth: 640 }}>
      <SettingsTable
        rows={[
          { name: "Gareth Price", value: "Workspace admin", meta: "Active", tone: "text-success" },
          { name: "data.steward", value: "Operator", meta: "Active", tone: "text-success" },
          { name: "Finance", value: "Viewer", meta: "Invited", tone: "text-warning" },
        ]}
      />
    </div>
  ),
};
