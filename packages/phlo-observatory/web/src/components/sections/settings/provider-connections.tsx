/**
 * Provider connection health: reachability, endpoint and last confirmed state.
 */
import type { ProviderConnection } from "@/api/types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function ProviderConnections({
  connections,
  title,
  meta,
}: {
  connections: Array<ProviderConnection>;
  title: string;
  meta: string;
}) {
  return (
    <div className="overflow-clip rounded-lg border border-border">
      <div className="flex items-center justify-between gap-2 border-b border-border bg-accent px-3.5 py-3">
        <span className="font-display text-[13px] leading-4.5 font-semibold text-accent-foreground">
          {title}
        </span>
        <span className="text-xs leading-4 text-accent-foreground/80">{meta}</span>
      </div>
      <div className="flex flex-col">
        {connections.map((connection, index) => (
          <div
            key={connection.name}
            className={cn(
              "flex items-center gap-3.5 px-3.5 py-3.25",
              index < connections.length - 1 && "border-b border-border",
            )}
          >
            <span
              className={cn(
                "size-2 shrink-0 rounded-[4px]",
                connection.degraded ? "bg-destructive" : "bg-success",
              )}
            />
            <div className="flex w-54 shrink-0 flex-col gap-0.5">
              <span className="text-[13px] leading-4.5 font-semibold">{connection.name}</span>
              <span className="text-[11px] leading-3.75 text-muted-foreground">
                {connection.role}
              </span>
            </div>
            <span className="w-65.5 shrink-0 truncate font-mono text-[11px] leading-3.75 text-[#525252] dark:text-muted-foreground">
              {connection.endpoint}
            </span>
            <div className="flex flex-1 flex-col gap-0.5">
              <span
                className={cn(
                  "text-xs leading-4",
                  connection.degraded ? "text-destructive" : "text-success",
                )}
              >
                {connection.state}
              </span>
              <span className="text-[11px] leading-3.75 text-muted-foreground">
                {connection.detail}
              </span>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="shrink-0 rounded-md text-accent-foreground"
            >
              {connection.action}
            </Button>
          </div>
        ))}
      </div>
    </div>
  );
}
