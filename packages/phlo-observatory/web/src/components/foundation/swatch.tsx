/**
 * Colour swatch for the design-system reference: reads the live CSS variable
 * so the palette shown is the palette in use.
 */
import * as React from "react";

import { cn } from "@/lib/utils";

export function Swatch({
  token,
  usage,
  className,
}: {
  /** CSS custom property name, e.g. `--primary`. */
  token: string;
  usage?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-2.5 rounded-lg border border-border bg-card p-2.5",
        className,
      )}
    >
      <span
        className="size-8 shrink-0 rounded-md border border-border"
        style={{ background: `var(${token})` }}
        aria-hidden
      />
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="truncate font-mono text-[11px]">{token}</span>
        {usage ? (
          <span className="truncate text-[10px] leading-3 text-muted-foreground">{usage}</span>
        ) : null}
      </span>
    </div>
  );
}
