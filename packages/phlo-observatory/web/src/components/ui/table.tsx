/**
 * Table primitive (shadcn registry, Base UI).
 */
import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Paper table chrome: violet header band, 33px data rows, hairline separators
 * with no outer border (the surrounding Card provides it).
 */
function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <div data-slot="table-container" className="w-full overflow-x-auto">
      <table
        data-slot="table"
        className={cn("w-full caption-bottom border-collapse text-xs", className)}
        {...props}
      />
    </div>
  );
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return <thead data-slot="table-header" className={cn("[&_tr]:bg-accent", className)} {...props} />;
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return <tbody data-slot="table-body" className={cn(className)} {...props} />;
}

function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn("border-t border-border bg-card", className)}
      {...props}
    />
  );
}

function TableRow({
  className,
  clickable,
  selected,
  ...props
}: React.ComponentProps<"tr"> & { clickable?: boolean; selected?: boolean }) {
  return (
    <tr
      data-slot="table-row"
      data-clickable={clickable ? "true" : undefined}
      aria-selected={selected ? true : undefined}
      className={cn(
        "border-t border-border first:border-t-0",
        clickable && "cursor-pointer hover:bg-primary/5",
        className,
      )}
      {...props}
    />
  );
}

function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        "h-7 px-3 text-left align-middle text-[11px] leading-3.5 font-medium whitespace-nowrap text-accent-foreground",
        className,
      )}
      {...props}
    />
  );
}

function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return (
    <td
      data-slot="table-cell"
      className={cn("h-[33px] px-3 align-middle text-xs", className)}
      {...props}
    />
  );
}

function TableCaption({ className, ...props }: React.ComponentProps<"caption">) {
  return (
    <caption
      data-slot="table-caption"
      className={cn("mt-2 text-[11px] text-muted-foreground", className)}
      {...props}
    />
  );
}

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableRow,
  TableHead,
  TableCell,
  TableCaption,
};
