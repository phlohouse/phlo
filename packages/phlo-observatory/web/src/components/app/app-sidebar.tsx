/**
 * App Sidebar component.
 */
import { Bot } from "lucide-react";
import { useNavigate } from "@tanstack/react-router";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { NAV_ITEMS } from "@/config/navigation";
import { cn } from "@/lib/utils";

/**
 * Left rail: brand block, primary navigation, and the signed-in operator.
 * Sticky and fixed-width; the workspace card scrolls independently beside it.
 */
export function AppSidebar({ pathname }: { pathname: string }) {
  const navigate = useNavigate();

  const isActive = (to: string) =>
    to === "/" ? pathname === "/" : pathname === to || pathname.startsWith(`${to}/`);

  return (
    <aside
      className="sticky top-3 flex h-[calc(100vh-1.5rem)] shrink-0 flex-col gap-6 px-1 py-3"
      style={{ width: "var(--sidebar-width)" }}
    >
      <div className="flex items-center gap-2.5 px-3 py-1">
        <span className="flex size-7.5 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <Bot className="size-5.5" strokeWidth={2.3} />
        </span>
        <span className="font-display text-[25px] leading-7.5 font-bold tracking-[-0.04em]">
          phlo
        </span>
      </div>

      <nav className="flex flex-1 flex-col gap-1.5">
        {NAV_ITEMS.map((item) => {
          const active = isActive(item.to);
          const Icon = item.icon;
          return (
            <button
              key={item.to}
              type="button"
              onClick={() => navigate({ to: item.to })}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex h-9.75 cursor-pointer items-center gap-2.5 rounded-[7px] px-3 text-sm transition-colors",
                active
                  ? "bg-accent font-semibold text-accent-foreground"
                  : "text-sidebar-foreground hover:bg-muted",
              )}
            >
              <Icon className="size-4.5 shrink-0" strokeWidth={1.6} />
              <span className="flex-1 text-left">{item.label}</span>
              {item.count !== undefined ? (
                <span className="text-xs text-sidebar-foreground">{item.count}</span>
              ) : null}
            </button>
          );
        })}
      </nav>

      <div className="flex flex-col gap-4.5 px-3 pb-1">
        <div className="h-px shrink-0 bg-[#d3d3d3] dark:bg-border" />
        <div className="flex items-center gap-2.5">
          <Avatar className="size-7.5 shrink-0 rounded-[15px] bg-[#ded9ee] dark:bg-secondary">
            <AvatarFallback className="bg-transparent text-[11px] font-semibold text-[#35277f] dark:text-secondary-foreground">
              GP
            </AvatarFallback>
          </Avatar>
          <div className="flex flex-col gap-0.5">
            <span className="text-[13px] leading-4 font-medium">Gareth Price</span>
            <span className="text-[11px] leading-3.5 text-muted-foreground">Workspace admin</span>
          </div>
        </div>
      </div>
    </aside>
  );
}
