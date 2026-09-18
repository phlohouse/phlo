/**
 * Page tab bar built on the Tabs primitive.
 *
 * Owns the full-width hairline under the tabs, so pages only declare tab
 * labels and panels rather than reproducing the border wrapper.
 */
import * as React from "react";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

export interface PageTab {
  value: string;
  label: React.ReactNode;
}

export function PageTabs({
  tabs,
  defaultValue,
  value,
  onValueChange,
  className,
  children,
}: {
  tabs: Array<PageTab>;
  defaultValue?: string;
  value?: string;
  onValueChange?: (value: string) => void;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <Tabs
      defaultValue={defaultValue}
      value={value}
      onValueChange={onValueChange}
      className={cn("gap-0", className)}
    >
      <div className="border-b border-border px-6">
        <TabsList className="border-b-0">
          {tabs.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value}>
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </div>
      {children}
    </Tabs>
  );
}

export { TabsContent } from "@/components/ui/tabs";
