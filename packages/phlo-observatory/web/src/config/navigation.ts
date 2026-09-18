/**
 * Primary navigation model for the Mission Control sidebar.
 */
import {
  BookOpen,
  Database,
  GitBranch,
  LayoutGrid,
  Play,
  Rows3,
  Settings,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  label: string;
  to: string;
  icon: LucideIcon;
  /** Trailing count badge, e.g. active runs or pending releases. */
  count?: number;
}

/**
 * Primary navigation. `to` values are router paths; the dev API returns live
 * counts once wired, which is why `count` is optional rather than derived.
 */
export const NAV_ITEMS: NavItem[] = [
  { label: "Overview", to: "/", icon: LayoutGrid },
  { label: "Data", to: "/datasets/orders", icon: Database },
  { label: "Runs", to: "/runs/orders-daily", icon: Play, count: 4 },
  { label: "Releases", to: "/releases", icon: GitBranch, count: 2 },
  { label: "Platform", to: "/platform", icon: Rows3 },
  { label: "Governance", to: "/governance", icon: ShieldCheck },
  { label: "Documentation", to: "/docs", icon: BookOpen },
  { label: "Settings", to: "/settings", icon: Settings },
];
