/**
 * Dataset lineage chain: upstream sources through this dataset to its
 * downstream consumers, with the current node highlighted.
 */
import { ArrowRight } from "lucide-react";

import { InlineLink, SectionHeader } from "@/components/layout/section-header";
import { cn } from "@/lib/utils";

export interface LineageNode {
  name: string;
  role: string;
  current?: boolean;
}

export function LineageChain({
  nodes,
  caption,
  onViewAll,
}: {
  nodes: Array<LineageNode>;
  caption: string;
  onViewAll?: () => void;
}) {
  return (
    <>
      <SectionHeader
        title="Lineage & consumers"
        action={<InlineLink onClick={onViewAll}>View full graph →</InlineLink>}
      />
      <div className="flex items-center gap-2.5">
        {nodes.map((node, index) => (
          <div key={node.name} className="flex flex-1 items-center gap-2.5 last:flex-none">
            <div
              className={cn(
                "min-w-0 flex-1 rounded-[7px] border px-2.5 py-3",
                node.current
                  ? "border-[#c6b8fa] bg-[#f4f1ff] dark:bg-secondary"
                  : "border-border bg-subtle",
              )}
            >
              <div
                className={cn(
                  "truncate text-xs font-semibold",
                  node.current ? "text-accent-foreground" : "text-foreground",
                )}
              >
                {node.name}
              </div>
              <div className="truncate text-[11px] leading-3.5 text-muted-foreground">
                {node.role}
              </div>
            </div>
            {index < nodes.length - 1 ? (
              <ArrowRight className="size-4.5 shrink-0 text-muted-foreground" />
            ) : null}
          </div>
        ))}
      </div>
      <p className="pt-2 text-[11px] leading-3.5 text-muted-foreground">{caption}</p>
    </>
  );
}
