/**
 * Command Palette component.
 */
import { useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";
import { useNavigate } from "@tanstack/react-router";

import type {SearchEntry} from "@/data/demo";
import {
  Dialog,
  DialogContent,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { NAV_ITEMS } from "@/config/navigation";
import {  searchEntries } from "@/data/demo";
import { cn } from "@/lib/utils";

interface CommandItem extends Omit<SearchEntry, "kind"> {
  kind: string;
}

/** ⌘K palette over navigation targets and the workspace search index. */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);

  const items = useMemo<Array<CommandItem>>(() => {
    const pages: Array<CommandItem> = NAV_ITEMS.map((item) => ({
      kind: "Go to",
      title: item.label,
      meta: "Page",
      to: item.to,
    }));
    const all = [...pages, ...searchEntries];
    const needle = query.trim().toLowerCase();
    if (!needle) return all.slice(0, 9);
    return all
      .filter((item) => `${item.kind} ${item.title} ${item.meta}`.toLowerCase().includes(needle))
      .slice(0, 9);
  }, [query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
    }
  }, [open]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  const run = (item: CommandItem) => {
    navigate({ to: item.to });
    onClose();
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogPortal>
        <DialogOverlay className="bg-black/35" />
        <DialogContent
          className="top-[12vh] w-[560px] max-w-[90vw] translate-y-0 gap-0 overflow-hidden p-0"
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setActiveIndex((i) => Math.min(i + 1, items.length - 1));
            }
            if (event.key === "ArrowUp") {
              event.preventDefault();
              setActiveIndex((i) => Math.max(i - 1, 0));
            }
            if (event.key === "Enter" && items[activeIndex]) {
              run(items[activeIndex]);
            }
          }}
        >
          <DialogTitle className="sr-only">Search Phlo</DialogTitle>
          <div className="flex items-center gap-2 border-b border-border px-3.5 py-3">
            <Search className="size-4 shrink-0 text-muted-foreground" />
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search Phlo"
              className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
            <kbd className="rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">
              esc
            </kbd>
          </div>
          <ScrollArea className="max-h-80">
            <div className="p-1.5">
              {items.length === 0 ? (
                <div className="px-3 py-4 text-xs text-muted-foreground">
                  No matches for “{query}”
                </div>
              ) : null}
              {items.map((item, index) => (
                <button
                  key={`${item.kind}-${item.title}`}
                  type="button"
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => run(item)}
                  className={cn(
                    "flex w-full cursor-pointer items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-xs",
                    index === activeIndex ? "bg-accent" : "hover:bg-muted",
                  )}
                >
                  <span className="w-16 shrink-0 text-[11px] text-muted-foreground">{item.kind}</span>
                  <span className="font-semibold">{item.title}</span>
                  <span className="ml-auto text-[11px] text-muted-foreground">{item.meta}</span>
                </button>
              ))}
            </div>
          </ScrollArea>
        </DialogContent>
      </DialogPortal>
    </Dialog>
  );
}
