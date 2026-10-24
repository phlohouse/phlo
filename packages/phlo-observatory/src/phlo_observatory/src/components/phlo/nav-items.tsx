/** Defines the primary Observatory navigation destinations and icons. */
import {
  BarChart3Icon,
  BoxIcon,
  DatabaseIcon,
  DownloadIcon,
  GitBranchIcon,
  LayoutGridIcon,
  SlidersHorizontalIcon,
  SparklesIcon,
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
    | '/sources'
    | '/data-products'
    | '/metrics'
    | '/query'
    | '/pipelines'
    | '/transform'
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
    to: '/sources',
    label: 'Sources',
    Icon: DownloadIcon,
    exact: false,
    mobile: false,
  },
  {
    to: '/data-products',
    label: 'Data products',
    Icon: BoxIcon,
    exact: false,
    mobile: false,
  },
  {
    to: '/metrics',
    label: 'Metrics',
    Icon: BarChart3Icon,
    exact: false,
    mobile: false,
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
    to: '/transform',
    label: 'Transform',
    Icon: SparklesIcon,
    exact: false,
    mobile: false,
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
