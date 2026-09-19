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
  const { environment, context } = useEnvironment();
  const data = context?.data ?? null;
  const observedAt = data?.observed_at
    ? new Date(data.observed_at).toLocaleTimeString([], { hour12: false })
    : null;

  return (
    <div className="flex min-h-screen gap-3 bg-background p-3">
      <AppSidebar pathname={pathname} />
      <div className="flex min-w-0 flex-1 flex-col overflow-clip rounded-xl border border-border bg-card">
        <AppTopbar crumbs={crumbsForPath(pathname)} context={context} />
        <main className="min-w-0 flex-1">
          <Outlet />
        </main>
        <footer className="flex h-7.5 shrink-0 items-center justify-between border-t border-border px-6 text-[10px] leading-3 text-muted-foreground">
          <span>
            {observedAt ? `Context observed ${observedAt}` : "Context unavailable"}
            {data && !data.read_ready ? " · reads degraded" : null}
          </span>
          <span>
            {data?.project_id ?? "Unconfigured project"}
            {environment ? ` · ${environment}` : ""}
            {data?.data_mode === "demo" ? " · fixture data" : ""}
          </span>
        </footer>
      </div>
    </div>
  );
}
