/**
 * Compact identifier: renders the leading characters of a hash or id in mono
 * with an ellipsis. The tooltip holds the full value; clicking copies it.
 * Row clicks are stopped so the copy gesture never navigates.
 */
import { useState } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export function Identifier({
  value,
  head = 10,
  className,
}: {
  value: string;
  /** Leading characters shown before the ellipsis. */
  head?: number;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const short = value.length <= head + 1 ? value : `${value.slice(0, head)}…`;
  return (
    <Tooltip>
      <TooltipTrigger
        className={cn(
          "cursor-pointer font-mono whitespace-nowrap hover:text-foreground",
          className,
        )}
        aria-label={value}
        onClick={(event) => {
          event.stopPropagation();
          void navigator.clipboard.writeText(value);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1200);
        }}
      >
        {short}
      </TooltipTrigger>
      <TooltipContent>
        <span className="block max-w-72 font-mono break-all">{value}</span>
        <span className="mt-0.5 block text-[10px] opacity-75">
          {copied ? "Copied" : "Click to copy"}
        </span>
      </TooltipContent>
    </Tooltip>
  );
}
