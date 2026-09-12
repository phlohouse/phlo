/**
 * Observatory navigation model. Groups capability pages into mission areas;
 * the shell renders these in order and appends extension-contributed pages.
 */
import {
  Activity,
  ArchiveRestore,
  Boxes,
  CirclePlay,
  Clipboard,
  Database,
  FileClock,
  FolderKanban,
  GitBranch,
  History,
  Import,
  LayoutDashboard,
  LayoutGrid,
  ListChecks,
  Logs,
  MonitorCheck,
  Package2,
  Plug,
  Search,
  Server,
  Settings,
  Table2,
  UploadCloud,
} from 'lucide-react'

export interface NavItem {
  id: string
  label: string
  path: string
  description: string
  icon?: typeof LayoutDashboard
}

export interface NavGroup {
  id: string
  label: string
  items: Array<NavItem>
}

export const NAV_GROUPS: Array<NavGroup> = [
  {
    id: 'command',
    label: 'Command',
    items: [
      {
        id: 'overview',
        label: 'Overview',
        path: '/',
        description: 'Lakehouse health, attention queue, and activity.',
        icon: LayoutDashboard,
      },
      {
        id: 'workspace',
        label: 'Workspace',
        path: '/workspace',
        description: 'Authored resources and project objects.',
        icon: FolderKanban,
      },
      {
        id: 'search',
        label: 'Search',
        path: '/search',
        description: 'Search across the whole lakehouse.',
        icon: Search,
      },
      {
        id: 'recents',
        label: 'Recents',
        path: '/recents',
        description: 'Recently opened resources in this browser.',
        icon: History,
      },
    ],
  },
  {
    id: 'catalog',
    label: 'Catalog',
    items: [
      {
        id: 'datasets',
        label: 'Datasets',
        path: '/datasets',
        description: 'Governed datasets and raw candidates.',
        icon: Boxes,
      },
      {
        id: 'tables',
        label: 'Tables',
        path: '/tables',
        description: 'Tables, previews, and row-level inspection.',
        icon: Table2,
      },
      {
        id: 'lineage',
        label: 'Lineage',
        path: '/lineage',
        description: 'Asset graph, dependencies, and metadata.',
        icon: Activity,
      },
      {
        id: 'queries',
        label: 'Query',
        path: '/queries',
        description: 'Read-only SQL workbench against Trino.',
        icon: Database,
      },
      {
        id: 'query-history',
        label: 'Query history',
        path: '/query-history',
        description: 'Browser-local query execution evidence.',
        icon: FileClock,
      },
    ],
  },
  {
    id: 'operations',
    label: 'Operations',
    items: [
      {
        id: 'runs',
        label: 'Runs',
        path: '/runs',
        description: 'Orchestrator history and run reports.',
        icon: CirclePlay,
      },
      {
        id: 'pipelines',
        label: 'Pipelines',
        path: '/pipelines',
        description: 'Dataset pipeline freshness and stages.',
        icon: Activity,
      },
      {
        id: 'quality',
        label: 'Quality',
        path: '/quality',
        description: 'Checks, severity, and evidence.',
        icon: ListChecks,
      },
      {
        id: 'operations',
        label: 'Recovery',
        path: '/operations',
        description: 'Failed work, recovery evidence, and actions.',
        icon: ArchiveRestore,
      },
      {
        id: 'continuity',
        label: 'Continuity',
        path: '/continuity',
        description: 'Backup, restore, maintenance, and upgrades.',
        icon: ArchiveRestore,
      },
      {
        id: 'logs',
        label: 'Logs',
        path: '/logs',
        description: 'Platform and resource events.',
        icon: Logs,
      },
    ],
  },
  {
    id: 'delivery',
    label: 'Delivery',
    items: [
      {
        id: 'publishing',
        label: 'Publishing',
        path: '/publishing',
        description: 'Release readiness for internal datasets.',
        icon: UploadCloud,
      },
      {
        id: 'branches',
        label: 'Change review',
        path: '/branches',
        description: 'Nessie branch state, diffs, and merges.',
        icon: GitBranch,
      },
      {
        id: 'governance',
        label: 'Governance',
        path: '/governance',
        description: 'Owners, classifications, and controls.',
        icon: MonitorCheck,
      },
    ],
  },
  {
    id: 'platform',
    label: 'Platform',
    items: [
      {
        id: 'services',
        label: 'Services',
        path: '/services',
        description: 'Runtime services and stack status.',
        icon: Server,
      },
      {
        id: 'ingestion',
        label: 'Ingestion',
        path: '/ingestion',
        description: 'Source onboarding and freshness.',
        icon: Import,
      },
      {
        id: 'workflows',
        label: 'Workflow builder',
        path: '/workflows/new',
        description: 'Compose a new workflow from contributions.',
        icon: Clipboard,
      },
      {
        id: 'storage',
        label: 'Storage',
        path: '/storage',
        description: 'Lakehouse storage and table providers.',
        icon: Package2,
      },
      {
        id: 'observability',
        label: 'Observability',
        path: '/observability',
        description: 'Metrics and trace providers.',
        icon: Activity,
      },
      {
        id: 'apis',
        label: 'APIs',
        path: '/apis',
        description: 'Published API surfaces.',
        icon: LayoutGrid,
      },
      {
        id: 'bi',
        label: 'BI surfaces',
        path: '/bi',
        description: 'Reports, dashboards, and consumers.',
        icon: LayoutDashboard,
      },
      {
        id: 'extensions',
        label: 'Extensions',
        path: '/extensions',
        description: 'Installed providers and their settings.',
        icon: Plug,
      },
      {
        id: 'settings',
        label: 'Settings',
        path: '/settings',
        description: 'Project and Observatory preferences.',
        icon: Settings,
      },
    ],
  },
]

export const ALL_NAV_ITEMS: Array<NavItem> = NAV_GROUPS.flatMap(
  (group) => group.items,
)

export function navItemForPath(pathname: string): NavItem | null {
  const clean = pathname.replace(/\/+$/, '') || '/'
  let best: NavItem | null = null
  for (const item of ALL_NAV_ITEMS) {
    if (item.path === '/' ? clean === '/' : clean.startsWith(item.path)) {
      if (!best || item.path.length > best.path.length) best = item
    }
  }
  return best
}
