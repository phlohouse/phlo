/**
 * Primary navigation model for the Mission Control sidebar.
 */
import {
  Database,
  GitBranch,
  LayoutGrid,

  Play,
  Rows3,
  Settings
} from "lucide-react";
import type {LucideIcon} from "lucide-react";

export interface NavItem {
  label: string;
  to: string;
  icon: LucideIcon;
  /**
   * Which live query feeds this item's trailing count badge. `undefined`
   * renders no badge — counts are never fabricated.
   */
  countSource?: "runs" | "releases";
}

/**
 * Primary navigation. `to` values are router paths to real collections.
 */
export const NAV_ITEMS: Array<NavItem> = [
  { label: "Overview", to: "/", icon: LayoutGrid },
  { label: "Data", to: "/datasets", icon: Database },
  { label: "Runs", to: "/runs", icon: Play, countSource: "runs" },
  { label: "Releases", to: "/releases", icon: GitBranch, countSource: "releases" },
  { label: "Platform", to: "/platform", icon: Rows3 },
  { label: "Settings", to: "/settings", icon: Settings },
];
