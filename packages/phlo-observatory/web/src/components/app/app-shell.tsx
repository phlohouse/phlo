/**
 * App Shell component.
 */
import { Outlet, useLocation } from "@tanstack/react-router";

import { AppSidebar } from "@/components/app/app-sidebar";
import { AppTopbar } from "@/components/app/app-topbar";
import { useEnvironment } from "@/components/app/environment-provider";
import { crumbsForPath } from "@/config/breadcrumbs";

/**
 * Application frame: fixed navigation rail beside a white workspace card that
 * owns the toolbar, scrollable content and the observation footer.
 *
 * The grey `background` token shows through the 12px gutter, matching Paper's
 * `w-360 p-3 gap-3 bg-app` root.
 */
export function AppShell() {
  const { pathname } = useLocation();
  const { environment, setEnvironment } = useEnvironment();

  return (
    <div className="flex min-h-screen gap-3 bg-background p-3">
      <AppSidebar pathname={pathname} />
      <div className="flex min-w-0 flex-1 flex-col overflow-clip rounded-xl border border-border bg-card">
        <AppTopbar
          crumbs={crumbsForPath(pathname)}
          environment={environment}
          onEnvironmentChange={setEnvironment}
        />
        <main className="min-w-0 flex-1">
          <Outlet />
        </main>
        <footer className="flex h-7.5 shrink-0 items-center justify-between border-t border-border px-6 text-[10px] leading-3 text-muted-foreground">
          <span>Updated 09:35:12 UTC · Auto-refresh 15s</span>
          <span>{environment} · Retail analytics · Example data</span>
        </footer>
      </div>
    </div>
  );
}
