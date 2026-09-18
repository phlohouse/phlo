/**
 * Tabs: the PageTabs underline bar and the pill (segmented) variant.
 */
import type { Meta, StoryObj } from "@storybook/react";

import { PageTabs, TabsContent } from "@/components/layout/page-tabs";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const meta: Meta = { title: "UI/Tabs" };
export default meta;

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "schema", label: "Schema · 8" },
  { value: "preview", label: "Preview" },
  { value: "quality", label: "Quality" },
  { value: "lineage", label: "Lineage" },
];

export const PageUnderlineTabs: StoryObj = {
  render: () => (
    <div style={{ width: 900 }}>
      <PageTabs tabs={TABS} defaultValue="overview">
        {TABS.map((tab) => (
          <TabsContent key={tab.value} value={tab.value}>
            <div className="px-6 py-4 text-xs text-muted-foreground">
              {tab.label} panel
            </div>
          </TabsContent>
        ))}
      </PageTabs>
    </div>
  ),
};

export const PillTabs: StoryObj = {
  render: () => (
    <Tabs defaultValue="all">
      <TabsList variant="pill">
        <TabsTrigger variant="pill" value="all">
          All 8
        </TabsTrigger>
        <TabsTrigger variant="pill" value="experiment">
          Experiment 2
        </TabsTrigger>
        <TabsTrigger variant="pill" value="sample">
          Sample 1
        </TabsTrigger>
        <TabsTrigger variant="pill" value="file">
          File 4
        </TabsTrigger>
      </TabsList>
    </Tabs>
  ),
};
