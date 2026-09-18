/**
 * Overview section: priority items ranked by consumer impact.
 *
 * Follows the same surface rules as the tables: white rows separated by
 * hairlines, a purple tint on hover, and a violet fill on the active row. The
 * top-priority item is active by default, matching Paper.
 *
 * Presentational — navigation is delegated so the block renders without a
 * router.
 */
import { AlertCircle, ChevronRight, Clock, GitBranch, type LucideIcon } from "lucide-react";

import { Section } from "@/components/layout/section-header";
import type { AttentionItem, Severity } from "@/data/demo";
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
  active,
  last,
  onOpen,
}: {
  item: AttentionItem;
  active: boolean;
  last: boolean;
  onOpen?: (item: AttentionItem) => void;
}) {
  const Icon = ICON[item.severity];
  return (
    <button
      type="button"
      onClick={() => onOpen?.(item)}
      aria-current={active ? "true" : undefined}
      className={cn(
        "flex h-14 shrink-0 cursor-pointer items-center gap-2.5 px-3 text-left transition-colors",
        !last && "border-b border-border",
        active ? "bg-secondary" : "hover:bg-primary/5",
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
  activeIndex = 0,
  onOpen,
}: {
  items: Array<AttentionItem>;
  /** Index of the highlighted row; pass null for none. */
  activeIndex?: number | null;
  onOpen?: (item: AttentionItem) => void;
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
            active={index === activeIndex}
            last={index === items.length - 1}
            onOpen={onOpen}
          />
        ))}
      </div>
    </Section>
  );
}
