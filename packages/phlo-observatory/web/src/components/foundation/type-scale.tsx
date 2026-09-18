/**
 * Type specimen row for the design-system reference.
 */
import * as React from "react";

import { cn } from "@/lib/utils";

export interface TypeSample {
  token: string;
  label: string;
  className: string;
}

export function TypeScaleTable({ samples }: { samples: Array<TypeSample> }) {
  return (
    <div className="overflow-clip rounded-[7px] border border-border">
      <div className="flex h-7 items-center bg-accent px-3 text-[11px] font-medium text-accent-foreground">
        <span className="w-40 shrink-0">Token</span>
        <span>Sample</span>
      </div>
      {samples.map((sample) => (
        <div
          key={sample.token}
          className="flex min-h-9 items-center border-t border-border px-3 first:border-t-0"
        >
          <span className="w-40 shrink-0 font-mono text-[11px] text-muted-foreground">
            {sample.token}
          </span>
          <span className={cn(sample.className)}>{sample.label}</span>
        </div>
      ))}
    </div>
  );
}
