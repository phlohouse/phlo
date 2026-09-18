/**
 * App Topbar Actions component.
 */
import { AlertTriangle, Check, ChevronDown, Moon, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ScrollArea } from "@/components/ui/scroll-area";
import { alerts, environments } from "@/data/demo";
import { useTheme } from "@/components/app/theme-provider";
import { cn } from "@/lib/utils";

const SEVERITY_DOT = {
  danger: "bg-destructive",
  warning: "bg-warning",
  accent: "bg-primary",
  muted: "bg-muted-foreground",
} as const;

export function EnvironmentSwitcher({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const dot =
    value === "Production" ? "bg-success" : value === "Staging" ? "bg-warning" : "bg-muted-foreground";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="flex cursor-pointer items-center gap-1.5 text-xs font-medium outline-none">
        <span className={cn("size-1.5 rounded-[3px]", dot)} />
        {value}
        <ChevronDown className="size-3.5 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuLabel>Environment</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {environments.map((environment) => (
          <DropdownMenuItem
            key={environment}
            onClick={() => onChange(environment)}
            className="justify-between text-xs"
          >
            {environment}
            {environment === value ? <Check className="size-3.5" /> : null}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function AlertsInbox() {
  const unread = alerts.filter((alert) => alert.severity !== "muted").length;
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
          {alerts.map((alert) => (
            <div key={alert.title} className="flex gap-2.5 border-b border-border px-4 py-2.5 last:border-b-0">
              <span
                className={cn(
                  "mt-1 size-2 shrink-0 rounded-full",
                  SEVERITY_DOT[alert.severity],
                )}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                  <span className="text-[13px] font-semibold">{alert.title}</span>
                  <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">{alert.time}</span>
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
