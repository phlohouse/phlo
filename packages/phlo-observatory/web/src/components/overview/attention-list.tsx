/**
 * Overview page section: attention list.
 */
import { AlertCircle, ChevronRight, Clock, GitBranch, type LucideIcon } from "lucide-react";
import { useNavigate } from "@tanstack/react-router";

import { SectionHeader } from "@/components/section-header";
import { attentionItems, type AttentionItem, type Severity } from "@/data/demo";
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

const ROW_BG: Record<Severity, string> = {
  danger: "bg-secondary",
  warning: "bg-secondary",
  accent: "bg-secondary",
  muted: "bg-secondary",
};

function AttentionRow({ item, first }: { item: AttentionItem; first: boolean }) {
  const navigate = useNavigate();
  const Icon = ICON[item.severity];
  return (
    <button
      type="button"
      onClick={() => navigate({ to: item.to })}
      className={cn(
        "flex h-14 shrink-0 cursor-pointer items-center gap-2.5 px-3 text-left transition-colors hover:bg-accent/60",
        first ? undefined : "border-t border-border",
        ROW_BG[item.severity],
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

/** "Needs attention" — priority items ranked by consumer impact. */
export function AttentionList() {
  return (
    <section>
      <SectionHeader title="Needs attention" meta="3 priority items · ranked by consumer impact" />
      <div className="flex flex-col overflow-clip rounded-[7px] border border-border">
        {attentionItems.map((item, index) => (
          <AttentionRow key={item.title} item={item} first={index === 0} />
        ))}
      </div>
    </section>
  );
}
