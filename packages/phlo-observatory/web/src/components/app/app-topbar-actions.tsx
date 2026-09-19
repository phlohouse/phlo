/**
 * App Topbar Actions component.
 */
import { AlertTriangle, Moon, Sun } from "lucide-react";

import { useQuery } from "@tanstack/react-query";
import type { MissionContext, ReadEnvelope } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ScrollArea } from "@/components/ui/scroll-area";
import { queries } from "@/api/mission-control";
import { useTheme } from "@/components/app/theme-provider";
import { cn } from "@/lib/utils";

const SEVERITY_DOT = {
  danger: "bg-destructive",
  warning: "bg-warning",
  accent: "bg-primary",
  muted: "bg-muted-foreground",
} as const;

const DEPENDENCY_DOT = {
  ready: "bg-success",
  unavailable: "bg-destructive",
  unconfigured: "bg-muted-foreground",
  unsupported: "bg-warning",
} as const;

/**
 * The deployment's one configured environment, plus a readiness popover. One
 * Observatory serves one project/environment — this is a server fact, so the
 * badge is display-only; the popover surfaces the context diagnostic.
 */
export function EnvironmentBadge({
  context,
}: {
  context: ReadEnvelope<MissionContext> | undefined;
}) {
  const data = context?.data ?? null;
  const environment = data?.environment_id ?? "—";
  const dot = !data
    ? "bg-muted-foreground"
    : data.control_ready
      ? "bg-success"
      : data.read_ready
        ? "bg-warning"
        : "bg-destructive";
  return (
    <Popover>
      <PopoverTrigger className="flex cursor-pointer items-center gap-1.5 text-xs font-medium outline-none">
        <span className={cn("size-1.5 rounded-[3px]", dot)} />
        {environment}
        {data?.data_mode === "demo" ? (
          <span className="text-muted-foreground">demo</span>
        ) : null}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="border-b border-border px-4 py-3">
          <div className="text-sm font-semibold">{data?.project_id ?? "No project"}</div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            {environment} · {data?.data_mode ?? "unknown"} mode
          </div>
        </div>
        <div className="flex items-center gap-4 border-b border-border px-4 py-2.5 text-xs">
          <span className="flex items-center gap-1.5">
            <span
              className={cn(
                "size-1.5 rounded-[3px]",
                data?.read_ready ? "bg-success" : "bg-destructive",
              )}
            />
            Read {data?.read_ready ? "ready" : "blocked"}
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className={cn(
                "size-1.5 rounded-[3px]",
                data?.control_ready ? "bg-success" : "bg-destructive",
              )}
            />
            Control {data?.control_ready ? "ready" : "blocked"}
          </span>
        </div>
        {data?.blockers.length ? (
          <div className="border-b border-border px-4 py-2.5">
            {data.blockers.map((blocker) => (
              <div key={blocker} className="text-xs text-muted-foreground">
                {blocker}
              </div>
            ))}
          </div>
        ) : null}
        <ScrollArea className="max-h-64">
          <div className="px-4 py-2.5">
            {data?.dependencies.map((dep) => (
              <div
                key={dep.name}
                className="flex items-center gap-2 py-0.5 text-xs"
              >
                <span
                  className={cn("size-1.5 shrink-0 rounded-[3px]", DEPENDENCY_DOT[dep.status])}
                />
                <span className="font-medium">{dep.name}</span>
                <span className="ml-auto truncate pl-3 text-muted-foreground">
                  {dep.detail ?? dep.status}
                </span>
              </div>
            ))}
            {!data ? (
              <div className="text-xs text-muted-foreground">Context unavailable</div>
            ) : null}
          </div>
        </ScrollArea>
      </PopoverContent>
    </Popover>
  );
}

export function AlertsInbox() {
  const alerts = useQuery(queries.alerts());
  const rows = alerts.data?.data ?? [];
  const unread = rows.filter((alert) => alert.severity !== "muted").length;
  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button variant="ghost" size="icon-sm" className="relative" aria-label="Alerts" />
        }
      >
        <AlertTriangle className="size-4.5" strokeWidth={1.6} />
        {unread > 0 ? (
          <span className="absolute -top-0.5 -right-0.5 size-2 rounded-full bg-destructive ring-2 ring-card" />
        ) : null}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[400px] p-0">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <span className="text-sm font-semibold">Alerts · {unread} new</span>
          <button type="button" className="cursor-pointer text-xs font-semibold text-accent-foreground">
            Rules →
          </button>
        </div>
        <ScrollArea className="max-h-80">
          {rows.map((alert) => (
            <div key={alert.id} className="flex gap-2.5 border-b border-border px-4 py-2.5 last:border-b-0">
              <span
                className={cn(
                  "mt-1 size-2 shrink-0 rounded-full",
                  SEVERITY_DOT[alert.severity],
                )}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className="text-[13px] font-semibold">{alert.title}</span>
                  <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">{alert.raised_at}</span>
                </div>
                <div className="mt-0.5 text-xs text-muted-foreground">{alert.detail}</div>
                {alert.action ? (
                  <button
                    type="button"
                    className="mt-1 cursor-pointer text-xs font-semibold text-accent-foreground"
                  >
                    {alert.action}
                  </button>
                ) : null}
              </div>
            </div>
          ))}
          {alerts.data && rows.length === 0 ? (
            <div className="px-4 py-6 text-center text-xs text-muted-foreground">
              {alerts.data.evidence.status === "demo"
                ? "No alerts in the demo fixture"
                : "No alerts recorded"}
            </div>
          ) : null}
          {alerts.isError ? (
            <div className="px-4 py-6 text-center text-xs text-muted-foreground">
              Alerts unavailable
            </div>
          ) : null}
        </ScrollArea>
        <div className="flex items-center justify-between px-4 py-2.5 text-xs text-muted-foreground">
          <span>Alerts report events — they are not evidence</span>
          <button type="button" className="cursor-pointer font-semibold text-accent-foreground">
            Audit →
          </button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      onClick={toggleTheme}
      aria-label={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
    >
      {theme === "light" ? (
        <Moon className="size-4.5" strokeWidth={1.6} />
      ) : (
        <Sun className="size-4.5" strokeWidth={1.6} />
      )}
    </Button>
  );
}
