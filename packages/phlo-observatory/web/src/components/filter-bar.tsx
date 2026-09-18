/**
 * Filter Bar component.
 */
import { Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

export interface FilterSelect {
  /** Placeholder / "all" label, e.g. "Status: all". */
  label: string;
  value: string;
  options: string[];
  onChange?: (value: string) => void;
}

export const FILTER_ALL = "__all__";

/**
 * Registry toolbar: free-text search, filter selects, then trailing actions.
 * Selects style down to Paper's 34px / 7px control size.
 */
export function FilterBar({
  search,
  onSearch,
  searchPlaceholder = "Search…",
  filters = [],
  actions,
  className,
}: {
  search?: string;
  onSearch?: (value: string) => void;
  searchPlaceholder?: string;
  filters?: FilterSelect[];
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      {onSearch ? (
        <div className="relative w-72">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search ?? ""}
            onChange={(event) => onSearch(event.target.value)}
            placeholder={searchPlaceholder}
            className="h-[34px] pl-8"
          />
        </div>
      ) : null}

      {filters.map((filter) => (
        <Select
          key={filter.label}
          value={filter.value}
          onValueChange={(value) => filter.onChange?.(String(value))}
        >
          <SelectTrigger className="h-[34px] w-auto gap-1.5 rounded-[7px] text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={FILTER_ALL}>{filter.label}</SelectItem>
            {filter.options.map((option) => (
              <SelectItem key={option} value={option}>
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ))}

      <div className="ml-auto flex items-center gap-2">{actions}</div>
    </div>
  );
}

/** Paper's trailing registry actions: Columns / Save view. */
export function ToolbarActions({ children }: { children?: React.ReactNode }) {
  return (
    <>
      <Button variant="outline" size="sm">
        Columns
      </Button>
      <Button variant="outline" size="sm">
        Save view
      </Button>
      {children}
    </>
  );
}
