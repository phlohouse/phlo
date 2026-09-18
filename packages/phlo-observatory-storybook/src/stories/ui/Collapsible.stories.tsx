/**
 * Collapsible: disclosure used by expandable panels and the trace waterfall.
 */
import type { Meta, StoryObj } from "@storybook/react";
import { ChevronRight } from "lucide-react";

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

const meta: Meta = { title: "UI/Collapsible" };
export default meta;

export const Disclosure: StoryObj = {
  render: () => (
    <div style={{ width: 520 }}>
      <Collapsible defaultOpen>
        <CollapsibleTrigger className="flex w-full cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
          <ChevronRight className="size-3.5" />
          Show data table (8 points)
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="mt-2 overflow-clip rounded-[7px] border border-border">
            {[
              ["4 °C", "4 °C", "0", "—"],
              ["4 °C", "4 °C", "7", "—"],
              ["25 °C", "25 °C", "28", "below spec"],
            ].map((row, index) => (
              <div
                key={index}
                className="flex items-center gap-2 border-t border-border px-3 py-2 text-xs first:border-t-0"
              >
                <span className="w-20">{row[0]}</span>
                <span className="w-20 text-muted-foreground">{row[1]}</span>
                <span className="text-muted-foreground">{row[2]} d</span>
                <span className="ml-auto text-destructive">{row[3]}</span>
              </div>
            ))}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  ),
};
