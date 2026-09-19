/**
 * App Topbar component.
 */
import { useEffect, useState } from "react";
import { ChevronRight, Search } from "lucide-react";

import type { MissionContext, ReadEnvelope } from "@/api/types";
import { AlertsInbox, EnvironmentBadge, ThemeToggle } from "@/components/app/app-topbar-actions";
import { CommandPalette } from "@/components/app/command-palette";
import { Button } from "@/components/ui/button";

export interface Crumb {
  label: string;
  to?: string;
}

/** Workspace bar: breadcrumb, search trigger, environment, alerts, theme. */
export function AppTopbar({
  crumbs,
  context,
}: {
  crumbs: Array<Crumb>;
  context: ReadEnvelope<MissionContext> | undefined;
}) {
  const [paletteOpen, setPaletteOpen] = useState(false);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <>
      <header className="flex h-11.5 shrink-0 items-center justify-between border-b border-border px-6">
        <nav
          aria-label="Breadcrumb"
          className="flex items-center gap-2.5 text-xs text-muted-foreground"
        >
          <span>Workspace</span>
          {crumbs.map((crumb, index) => {
            const isLast = index === crumbs.length - 1;
            return (
              <span key={`${crumb.label}-${index}`} className="flex items-center gap-2.5">
                <ChevronRight className="size-3.5 text-foreground/60" />
                <span className={isLast ? "font-semibold text-foreground" : undefined}>
                  {crumb.label}
                </span>
              </span>
            );
          })}
        </nav>

        <div className="flex items-center gap-4.5">
          <Button
            variant="outline"
            onClick={() => setPaletteOpen(true)}
            className="h-[34px] w-64 justify-start gap-2 font-normal text-muted-foreground"
          >
            <Search className="size-3.5" />
            <span className="flex-1 text-left">Search Phlo</span>
            <kbd className="text-[11px] text-muted-foreground">⌘ K</kbd>
          </Button>
          <EnvironmentBadge context={context} />
          <AlertsInbox />
          <ThemeToggle />
        </div>
      </header>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </>
  );
}
