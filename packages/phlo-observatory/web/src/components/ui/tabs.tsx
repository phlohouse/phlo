/**
 * Tabs primitive (shadcn registry, Base UI).
 */
"use client";

import { Tabs as TabsPrimitive } from "@base-ui/react/tabs";
import { cn } from "@/lib/utils";

/**
 * Paper tab bar: 38px underline tabs with an accent indicator. `pill` renders
 * the segmented control used for filters (e.g. All / Experiment / Sample).
 */
function Tabs({ className, ...props }: TabsPrimitive.Root.Props) {
  return (
    <TabsPrimitive.Root
      data-slot="tabs"
      className={cn("group/tabs flex flex-col", className)}
      {...props}
    />
  );
}

function TabsList({
  className,
  variant = "line",
  ...props
}: TabsPrimitive.List.Props & { variant?: "line" | "pill" }) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      data-variant={variant}
      className={cn(
        "inline-flex w-fit items-center",
        variant === "line"
          ? "gap-[26px] border-b border-border"
          : "gap-0.5 rounded-lg border border-border bg-card p-1",
        className,
      )}
      {...props}
    />
  );
}

function TabsTrigger({
  className,
  variant = "line",
  ...props
}: TabsPrimitive.Tab.Props & { variant?: "line" | "pill" }) {
  return (
    <TabsPrimitive.Tab
      data-slot="tabs-trigger"
      className={cn(
        "inline-flex cursor-pointer items-center gap-1.5 text-xs whitespace-nowrap transition-colors outline-none select-none focus-visible:ring-2 focus-visible:ring-ring/40 disabled:pointer-events-none disabled:opacity-50 data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0",
        variant === "line"
          ? "h-[38px] border-b-2 border-transparent text-muted-foreground hover:text-foreground data-[selected]:border-primary data-[selected]:font-semibold data-[selected]:text-accent-foreground"
          : "h-7 rounded-md px-2.5 text-muted-foreground hover:text-foreground data-[selected]:bg-card data-[selected]:font-semibold data-[selected]:text-foreground data-[selected]:shadow-xs",
        className,
      )}
      {...props}
    />
  );
}

function TabsContent({ className, ...props }: TabsPrimitive.Panel.Props) {
  return (
    <TabsPrimitive.Panel
      data-slot="tabs-content"
      className={cn("flex-1 outline-none", className)}
      {...props}
    />
  );
}

export { Tabs, TabsList, TabsTrigger, TabsContent };
