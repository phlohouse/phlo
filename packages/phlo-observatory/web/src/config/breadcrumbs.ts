/**
 * Breadcrumb trail lookup for the workspace topbar.
 */
import type { Crumb } from "@/components/app-topbar";

/**
 * Breadcrumb trail for a pathname. Kept as an explicit table rather than
 * derived from the router so labels can differ from route segments (for
 * example the run id shown in monospace).
 */
const ROUTES: Array<{ prefix: string; crumbs: Array<Crumb> }> = [
  { prefix: "/datasets", crumbs: [{ label: "Data", to: "/datasets/orders" }, { label: "Orders" }] },
  {
    prefix: "/runs",
    crumbs: [{ label: "Runs", to: "/runs/orders-daily" }, { label: "orders_daily" }],
  },
  { prefix: "/releases", crumbs: [{ label: "Releases" }] },
  { prefix: "/platform", crumbs: [{ label: "Platform" }] },
  { prefix: "/governance", crumbs: [{ label: "Governance" }] },
  { prefix: "/docs", crumbs: [{ label: "Documentation" }] },
  { prefix: "/reference", crumbs: [{ label: "Reference" }] },
  { prefix: "/settings", crumbs: [{ label: "Settings" }] },
];

export function crumbsForPath(pathname: string): Array<Crumb> {
  if (pathname === "/") return [{ label: "Overview" }];
  for (const route of ROUTES) {
    if (pathname === route.prefix || pathname.startsWith(`${route.prefix}/`)) {
      return route.crumbs;
    }
  }
  return [{ label: pathname.slice(1) }];
}
