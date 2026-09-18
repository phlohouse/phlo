/**
 * Overview section: priority items ranked by consumer impact.
 *
 * Rows follow the table surface rules exactly: white by default, hairline
 * separators, and a purple tint on hover. There is no persistent selection
 * fill — Paper's first-row violet is read as a captured hover state.
 *
 * Presentational — navigation is delegated so the block renders without a
 * router.
 */
import { AlertCircle, ChevronRight, Clock, GitBranch  } from "lucide-react";
import type {LucideIcon} from "lucide-react";

import type { MissionAttentionItem, Severity } from "@/api/types";
import { Section } from "@/components/layout/section-header";
import { cn } from "@/lib/utils";

const ICON: Record<Severity, LucideIcon> = {
  danger: AlertCircle,
  warning: Clock,
  accent: Clock,
  muted: GitBranch,
};

const ICON_COLOR: Record<Severity, string> = {
  danger: "text-destructive",
  warning: "text-warning",
  accent: "text-primary",
  muted: "text-muted-foreground",
};

function AttentionRow({
  item,
  last,
  onOpen,
}: {
  item: MissionAttentionItem;
  last: boolean;
  onOpen?: (item: MissionAttentionItem) => void;
}) {
  const Icon = ICON[item.severity];
  return (
    <button
      type="button"
      onClick={() => onOpen?.(item)}
      className={cn(
        "flex h-14 shrink-0 cursor-pointer items-center gap-2.5 px-3 text-left transition-colors hover:bg-primary/5",
        !last && "border-b border-border",
      )}
    >
      <Icon className={cn("size-4.5 shrink-0", ICON_COLOR[item.severity])} strokeWidth={1.6} />
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="truncate text-[13px] leading-4 font-semibold text-foreground">
          {item.title}
        </span>
        <span className="truncate text-[11px] leading-3.5 text-muted-foreground">{item.detail}</span>
      </span>
      <span className="flex shrink-0 items-center gap-0.75 text-[11px] leading-3.5 text-accent-foreground">
        {item.action}
        <ChevronRight className="size-4.5" strokeWidth={1.6} />
      </span>
    </button>
  );
}

export function AttentionList({
  items,
  onOpen,
}: {
  items: Array<MissionAttentionItem>;
  onOpen?: (item: MissionAttentionItem) => void;
}) {
  return (
    <Section
      title="Needs attention"
      meta={`${items.length} priority items · ranked by consumer impact`}
    >
      <div className="flex flex-col overflow-clip rounded-[7px] border border-border">
        {items.map((item, index) => (
          <AttentionRow
            key={item.title}
            item={item}
            last={index === items.length - 1}
            onOpen={onOpen}
          />
        ))}
      </div>
    </Section>
  );
}
