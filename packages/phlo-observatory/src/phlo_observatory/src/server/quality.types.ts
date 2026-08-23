/**
 * Shared types for data-quality checks and their recent executions as
 * surfaced by the Observatory.
 */
export type MetadataValue =
  | string
  | number
  | boolean
  | null
  | undefined
  | { [key: string]: {} }
  | Array<{}>

export interface QualityCheck {
  name: string
  assetKey: Array<string>
  description?: string
  severity: 'ERROR' | 'WARN'
  status: 'PASSED' | 'FAILED' | 'SKIPPED' | 'IN_PROGRESS'
  lastExecutionTime?: string
  lastResult?: {
    passed: boolean
    metadata?: Record<string, MetadataValue>
  }
}

interface CheckExecution {
  timestamp: string
  passed: boolean
  runId?: string
  metadata?: Record<string, MetadataValue>
}

export interface RecentCheckExecution extends CheckExecution {
  assetKey: Array<string>
  checkName: string
  status: 'PASSED' | 'FAILED' | 'SKIPPED' | 'IN_PROGRESS'
  severity: 'ERROR' | 'WARN'
}
