/** Defines the primary Observatory navigation destinations and icons. */
import {
  DatabaseIcon,
  GitBranchIcon,
  LayoutGridIcon,
  SlidersHorizontalIcon,
  SquareTerminalIcon,
  TriangleAlertIcon,
  WorkflowIcon,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

type NavItem = {
  to:
    | '/'
    | '/incidents'
    | '/assets'
    | '/query'
    | '/pipelines'
    | '/branches'
    | '/settings'
  label: string
  Icon: LucideIcon
  exact: boolean
  /** Shown in the phone tab bar */
  mobile: boolean
}

export const navItems: Array<NavItem> = [
  {
    to: '/',
    label: 'Overview',
    Icon: LayoutGridIcon,
    exact: true,
    mobile: true,
  },
  {
    to: '/incidents',
    label: 'Incidents',
    Icon: TriangleAlertIcon,
    exact: false,
    mobile: true,
  },
  {
    to: '/assets',
    label: 'Assets',
    Icon: DatabaseIcon,
    exact: false,
    mobile: true,
  },
  {
    to: '/query',
    label: 'Query',
    Icon: SquareTerminalIcon,
    exact: false,
    mobile: false,
  },
  {
    to: '/pipelines',
    label: 'Pipelines',
    Icon: WorkflowIcon,
    exact: false,
    mobile: true,
  },
  {
    to: '/branches',
    label: 'Branches',
    Icon: GitBranchIcon,
    exact: false,
    mobile: false,
  },
  {
    to: '/settings',
    label: 'Settings',
    Icon: SlidersHorizontalIcon,
    exact: false,
    mobile: false,
  },
]
