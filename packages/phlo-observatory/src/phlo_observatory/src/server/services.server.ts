/**
 * Services Server Functions
 *
 * Server-side functions for service discovery and Docker management.
 * Reads service.yaml files and interacts with Docker Compose.
 */

import { exec, execFile } from 'node:child_process'
import { existsSync } from 'node:fs'
import { readFile, readdir } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { promisify } from 'node:util'

import { createServerFn } from '@tanstack/react-start'
import { parse as parseYaml } from 'yaml'

import { authMiddleware } from '@/observatory/api/auth'
import {
  getComposeLabelValue,
  matchesComposeProject,
} from '@/server/docker-labels'
import { fnLogger } from '@/server/logger.server'
import { apiPost } from '@/server/phlo-api'

const execAsync = promisify(exec)
const execFileAsync = promisify(execFile)
const phloCommand = process.env.PHLO_CLI_COMMAND ?? 'phlo'
const phloProjectPath = process.env.PHLO_PROJECT_PATH
const envFilePath = process.env.ENV_FILE_PATH
const servicesCacheTtlMs = Number(
  process.env.PHLO_SERVICES_CACHE_TTL_MS ?? 5000,
)
const ENV_LINE_RE = /^([^=]+)=(.*)$/

let servicesCache: {
  timestamp: number
  data: Array<ServiceWithStatus>
} | null = null
let composeProjectCache: string | null | undefined
const servicesLog = fnLogger('services.server')
type ServiceControlAction = 'start' | 'stop' | 'restart'

type ServiceActionResult = {
  status: 'succeeded' | 'failed' | 'skipped'
  message?: string
}

const serviceMetadata: Record<
  string,
  { category: string; description: string; default: boolean }
> = {
  postgres: {
    category: 'core',
    description: 'PostgreSQL metadata and catalog store',
    default: true,
  },
  minio: {
    category: 'core',
    description: 'S3-compatible object storage',
    default: true,
  },
  'minio-setup': {
    category: 'core',
    description: 'Initializes MinIO buckets and policies',
    default: true,
  },
  nessie: {
    category: 'core',
    description: 'Git-like catalog for Iceberg tables',
    default: true,
  },
  trino: {
    category: 'core',
    description: 'Distributed SQL query engine',
    default: true,
  },
  'dagster-webserver': {
    category: 'orchestration',
    description: 'Dagster UI and GraphQL API',
    default: true,
  },
  'dagster-daemon': {
    category: 'orchestration',
    description: 'Dagster daemon for schedules and sensors',
    default: true,
  },
  observatory: {
    category: 'orchestration',
    description: 'Phlo Observatory UI',
    default: true,
  },
  pgweb: {
    category: 'admin',
    description: 'PostgreSQL web client',
    default: false,
  },
  superset: {
    category: 'bi',
    description: 'BI dashboards and exploration',
    default: false,
  },
  postgrest: {
    category: 'api',
    description: 'REST API for PostgreSQL',
    default: false,
  },
  hasura: {
    category: 'api',
    description: 'GraphQL API for PostgreSQL',
    default: false,
  },
  'phlo-api': {
    category: 'api',
    description: 'Backend API for Observatory and Phlo internals',
    default: true,
  },
  prometheus: {
    category: 'observability',
    description: 'Metrics collection and scraping',
    default: false,
  },
  grafana: {
    category: 'observability',
    description: 'Dashboards and monitoring UI',
    default: false,
  },
  loki: {
    category: 'observability',
    description: 'Log aggregation',
    default: false,
  },
  alloy: {
    category: 'observability',
    description: 'Metrics and log agent',
    default: false,
  },
}

interface CliServiceDefinition {
  name: string
  description?: string
  category?: string
  default?: boolean
  profile?: string | null
  depends_on?: Array<string>
  compose?: {
    ports?: Array<string>
  }
  env_vars?: Record<
    string,
    {
      default?: string | number
      description?: string
      secret?: boolean
    }
  >
}

// Types for service definitions
interface EnvVar {
  name: string
  value: string
  description?: string
  secret: boolean
}

export interface ServiceDefinition {
  name: string
  description: string
  category: string
  default: boolean
  image?: string
  dependsOn: Array<string>
  ports: Array<{ host: number; container: number; description?: string }>
  envVars: Array<EnvVar>
  url?: string
}

export interface DockerContainerStatus {
  name: string
  service: string
  status: 'running' | 'stopped' | 'unhealthy' | 'starting' | 'unknown'
  health?: string
  ports?: string
}

export interface ServiceWithStatus extends ServiceDefinition {
  containerStatus: DockerContainerStatus | null
}

interface DockerPsEntry {
  Name?: string
  Names?: string
  Labels?: string
  Health?: string
  Ports?: string
  State?: string
  Status?: string
}

interface NativeProcessEntry {
  pid: number
  started_at?: number
  log?: string
}

function isPidRunning(pid: number): boolean {
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

async function loadNativeProcesses(): Promise<
  Record<string, NativeProcessEntry>
> {
  const root = phloProjectPath ?? process.cwd()
  const statePath = join(root, '.phlo', 'native-processes.json')
  try {
    const raw = await readFile(statePath, 'utf-8')
    return JSON.parse(raw) as Record<string, NativeProcessEntry>
  } catch {
    return {}
  }
}

// Resolve the compose project once and memoize it. Inside the Observatory
// container, Docker sets HOSTNAME to the container id, which lets us inspect
// our own labels to discover the project without extra configuration.
async function getComposeProjectName(): Promise<string | null> {
  if (composeProjectCache !== undefined) {
    return composeProjectCache
  }

  const explicitProject =
    process.env.PHLO_COMPOSE_PROJECT ?? process.env.COMPOSE_PROJECT_NAME
  if (explicitProject) {
    composeProjectCache = explicitProject
    return composeProjectCache
  }

  const currentContainerId = process.env.HOSTNAME
  if (!currentContainerId) {
    composeProjectCache = null
    return composeProjectCache
  }

  try {
    const { stdout } = await execFileAsync('docker', [
      'inspect',
      currentContainerId,
      '--format',
      '{{ index .Config.Labels "com.docker.compose.project" }}',
    ])
    const project = String(stdout).trim()
    composeProjectCache = project || null
  } catch {
    composeProjectCache = null
  }

  return composeProjectCache
}

// Get the packages directory path for local fallback discovery.
// In Docker dev: /app/packages
// Locally: process.cwd() is packages/phlo-observatory/src/phlo_observatory
const getPackagesPath = (): string => {
  if (process.env.PHLO_PACKAGES_PATH) {
    return process.env.PHLO_PACKAGES_PATH
  }
  const dockerPath = '/app/packages'
  if (existsSync(dockerPath)) {
    return dockerPath
  }
  const localRoot = join(process.cwd(), '..', '..', '..')
  const localPackagesPath = join(localRoot, 'packages')
  if (existsSync(localPackagesPath)) {
    return localPackagesPath
  }
  const projectRoot = phloProjectPath ?? process.cwd()
  const projectPackages = join(projectRoot, 'packages')
  if (existsSync(projectPackages)) {
    return projectPackages
  }
  return localRoot
}

// Path to .phlo/.env file
const getEnvPath = (): string => {
  if (envFilePath) {
    return envFilePath
  }
  const candidates = [
    '/app/.phlo/.env',
    join(process.cwd(), '..', '..', '.phlo', '.env'),
    join(process.cwd(), '.phlo', '.env'),
  ]
  for (const candidate of candidates) {
    if (existsSync(candidate)) {
      return candidate
    }
  }
  return candidates[candidates.length - 1]
}

async function parseEnvFile(
  envPath: string,
  values: Record<string, string>,
): Promise<void> {
  try {
    const content = await readFile(envPath, 'utf-8')
    for (const line of content.split('\n')) {
      const trimmed = line.trim()
      if (trimmed && !trimmed.startsWith('#')) {
        const match = ENV_LINE_RE.exec(trimmed)
        if (match) {
          const key = match[1]
          const value = match[2]
          values[key] = value
        }
      }
    }
  } catch {
    // .env file may not exist
  }
}

function buildServiceDefinition(
  data: CliServiceDefinition,
): ServiceDefinition | null {
  if (!data.name) {
    return null
  }

  const ports: Array<{
    host: number
    container: number
    description?: string
  }> = []
  if (data.compose?.ports) {
    for (const portMapping of data.compose.ports) {
      const match = portMapping.match(
        /\$\{([^:}]+):-?(\d+)\}:(\d+)|(\d+):(\d+)/,
      )
      if (match) {
        if (match[1]) {
          ports.push({
            host: parseInt(match[2], 10),
            container: parseInt(match[3], 10),
            description: match[1],
          })
        } else {
          ports.push({
            host: parseInt(match[4], 10),
            container: parseInt(match[5], 10),
          })
        }
      }
    }
  }

  const envVars: Array<EnvVar> = []
  if (data.env_vars) {
    for (const [varName, config] of Object.entries(data.env_vars)) {
      envVars.push({
        name: varName,
        value: String(config.default ?? ''),
        description: config.description,
        secret: config.secret ?? false,
      })
    }
  }

  const firstPort = ports[0]
  const url = firstPort ? `http://localhost:${firstPort.host}` : undefined

  return {
    name: data.name,
    description: data.description || '',
    category: data.category || 'core',
    default: data.default ?? false,
    dependsOn: data.depends_on || [],
    ports,
    envVars,
    url,
  }
}

async function parseServiceYaml(
  filePath: string,
): Promise<ServiceDefinition | null> {
  try {
    const content = await readFile(filePath, 'utf-8')
    const data = parseYaml(content)
    return buildServiceDefinition(data)
  } catch {
    return null
  }
}

/**
 * Discover all services from service.yaml files
 */
async function discoverServices(): Promise<Array<ServiceDefinition>> {
  const startedAt = performance.now()
  const useCli = process.env.PHLO_USE_CLI_SERVICE_DISCOVERY === 'true'
  servicesLog.info({ useCli }, 'services_discovery_started')

  if (useCli) {
    const cliServices = await discoverServicesFromCli()
    if (cliServices.length > 0) {
      servicesLog.info(
        {
          source: 'cli',
          count: cliServices.length,
          durationMs: Math.round(performance.now() - startedAt),
        },
        'services_discovery_completed',
      )
      return cliServices
    }
  }

  const packagesPath = getPackagesPath()
  const services: Array<ServiceDefinition> = []

  try {
    const yamlFiles = await findServiceYamlFiles(packagesPath)
    const discovered = await Promise.all(yamlFiles.map(parseServiceYaml))
    for (const service of discovered) {
      if (service) {
        services.push(service)
      }
    }
  } catch (error) {
    servicesLog.error(
      { err: error, packagesPath },
      'services_discovery_scan_failed',
    )
  }

  if (shouldFallbackToCliDiscovery(useCli, services.length)) {
    servicesLog.warn(
      { source: 'filesystem', discoveredCount: services.length },
      'services_discovery_cli_fallback',
    )
    const cliServices = await discoverServicesFromCli()
    if (cliServices.length > 0) {
      servicesLog.info(
        {
          source: 'cli_fallback',
          count: cliServices.length,
          durationMs: Math.round(performance.now() - startedAt),
        },
        'services_discovery_completed',
      )
      return cliServices
    }
  }

  const sorted = services.sort((a, b) => {
    // Sort by category, then by name
    if (a.category !== b.category) {
      return a.category.localeCompare(b.category)
    }
    return a.name.localeCompare(b.name)
  })
  servicesLog.info(
    {
      source: 'filesystem',
      count: sorted.length,
      durationMs: Math.round(performance.now() - startedAt),
    },
    'services_discovery_completed',
  )
  return sorted
}

export function shouldFallbackToCliDiscovery(
  useCliDiscovery: boolean,
  discoveredServiceCount: number,
): boolean {
  return !useCliDiscovery && discoveredServiceCount === 0
}

async function findServiceYamlFiles(root: string): Promise<Array<string>> {
  const results: Array<string> = []
  const entries = await readdir(root, { withFileTypes: true })
  const directories: Array<string> = []

  for (const entry of entries) {
    if (entry.name.startsWith('.')) {
      continue
    }
    if (
      entry.name === 'node_modules' ||
      entry.name === 'dist' ||
      entry.name === 'build'
    ) {
      continue
    }
    const entryPath = join(root, entry.name)
    if (entry.isDirectory()) {
      directories.push(entryPath)
      continue
    }
    if (entry.isFile() && entry.name === 'service.yaml') {
      results.push(entryPath)
    }
  }
  const nestedResults = await Promise.all(directories.map(findServiceYamlFiles))
  for (const nested of nestedResults) {
    results.push(...nested)
  }

  return results
}

async function discoverServicesFromContainers(): Promise<
  Array<ServiceDefinition>
> {
  const startedAt = performance.now()
  try {
    const composeProject = await getComposeProjectName()
    const { stdout } = await execAsync('docker ps -a --format json')
    const services: Array<ServiceDefinition> = []
    const seen = new Set<string>()

    for (const line of stdout.trim().split('\n')) {
      if (!line) continue
      const container = JSON.parse(line) as {
        Labels?: string
        Ports?: string
      }

      const labels = container.Labels || ''
      if (!matchesComposeProject(labels, composeProject)) {
        continue
      }

      const serviceName =
        getComposeLabelValue(labels, 'com.docker.compose.service') || ''
      if (!serviceName || seen.has(serviceName)) {
        continue
      }
      seen.add(serviceName)

      const ports = parsePorts(container.Ports)
      const firstPort = ports[0]
      const metadata = serviceMetadata[serviceName]

      services.push({
        name: serviceName,
        description: metadata?.description ?? '',
        category: metadata?.category ?? 'core',
        default: metadata?.default ?? false,
        dependsOn: [],
        ports,
        envVars: [],
        url: firstPort ? `http://localhost:${firstPort.host}` : undefined,
      })
    }

    servicesLog.info(
      {
        source: 'containers',
        composeProject,
        count: services.length,
        durationMs: Math.round(performance.now() - startedAt),
      },
      'services_container_discovery_completed',
    )
    return services
  } catch (error) {
    servicesLog.warn({ err: error }, 'services_container_discovery_failed')
    return []
  }
}

function parsePorts(
  portsRaw?: string,
): Array<{ host: number; container: number; description?: string }> {
  if (!portsRaw) {
    return []
  }

  const ports: Array<{
    host: number
    container: number
    description?: string
  }> = []
  for (const entry of portsRaw.split(',')) {
    const match = entry.match(/:(\d+)->(\d+)/)
    if (!match) {
      continue
    }
    ports.push({
      host: parseInt(match[1], 10),
      container: parseInt(match[2], 10),
    })
  }
  return ports
}

// Ranking used when one compose service runs multiple containers: the highest
// priority status represents the service (running beats unhealthy beats
// starting beats stopped).
function statusPriority(status: DockerContainerStatus['status']): number {
  switch (status) {
    case 'running':
      return 4
    case 'unhealthy':
      return 3
    case 'starting':
      return 2
    case 'stopped':
      return 1
    default:
      return 0
  }
}

export function parseContainerStateStatus(
  container: Pick<DockerPsEntry, 'State' | 'Status' | 'Health'>,
): DockerContainerStatus['status'] {
  const rawState = (container.State || container.Status || '').toLowerCase()
  if (
    rawState.includes('running') ||
    rawState.startsWith('up ') ||
    rawState === 'up'
  ) {
    return container.Health === 'unhealthy' ? 'unhealthy' : 'running'
  }
  if (rawState.includes('starting') || rawState.includes('created')) {
    return 'starting'
  }
  if (rawState.length === 0) {
    return 'unknown'
  }
  return 'stopped'
}

export function parseDockerStatusLines(
  stdout: string,
  composeProject: string | null,
): Array<DockerContainerStatus> {
  const containersByService = new Map<string, DockerContainerStatus>()
  for (const line of stdout.trim().split('\n')) {
    if (!line) continue

    try {
      const container = JSON.parse(line) as DockerPsEntry
      const labels = container.Labels || ''
      if (!matchesComposeProject(labels, composeProject)) {
        continue
      }

      const serviceName =
        getComposeLabelValue(labels, 'com.docker.compose.service') || ''
      if (!serviceName) {
        continue
      }

      const parsedStatus: DockerContainerStatus = {
        name: container.Names || container.Name || '',
        service: serviceName,
        status: parseContainerStateStatus(container),
        health: container.Health,
        ports: container.Ports,
      }
      const existing = containersByService.get(serviceName)
      if (
        !existing ||
        statusPriority(parsedStatus.status) > statusPriority(existing.status)
      ) {
        containersByService.set(serviceName, parsedStatus)
      }
    } catch {
      // Skip invalid JSON lines
    }
  }

  return Array.from(containersByService.values())
}

async function discoverServicesFromCli(): Promise<Array<ServiceDefinition>> {
  const startedAt = performance.now()
  try {
    const execOptions = phloProjectPath ? { cwd: phloProjectPath } : undefined
    const { stdout } = await execAsync(
      `${phloCommand} services list --json`,
      execOptions,
    )
    const parsed = JSON.parse(stdout.toString()) as Array<CliServiceDefinition>
    const services: Array<ServiceDefinition> = []
    for (const service of parsed) {
      const definition = buildServiceDefinition(service)
      if (definition) {
        services.push(definition)
      }
    }
    servicesLog.info(
      {
        source: 'cli',
        count: services.length,
        durationMs: Math.round(performance.now() - startedAt),
      },
      'services_cli_discovery_completed',
    )
    return services
  } catch (error) {
    servicesLog.warn(
      { err: error, durationMs: Math.round(performance.now() - startedAt) },
      'services_cli_discovery_failed',
    )
    return discoverServicesFromContainers()
  }
}

/**
 * Parse env files and merge with service defaults
 */
async function loadEnvValues(): Promise<Record<string, string>> {
  const envPath = getEnvPath()
  const values: Record<string, string> = {}

  await parseEnvFile(envPath, values)

  const localEnvPath = envPath.endsWith('.env')
    ? `${envPath}.local`
    : join(dirname(envPath), '.env.local')
  if (existsSync(localEnvPath)) {
    await parseEnvFile(localEnvPath, values)
  }

  return values
}

/**
 * Get Docker container status for all services
 */
const getDockerStatus = createServerFn().handler(
  async (): Promise<Array<DockerContainerStatus>> => {
    const startedAt = performance.now()
    try {
      const composeProject = await getComposeProjectName()
      // Use docker ps to get ALL running containers (not compose-specific)
      const { stdout } = await execAsync('docker ps -a --format json')
      const statuses = parseDockerStatusLines(stdout, composeProject)
      servicesLog.info(
        {
          composeProject,
          count: statuses.length,
          durationMs: Math.round(performance.now() - startedAt),
        },
        'services_docker_status_completed',
      )
      return statuses
    } catch (error) {
      servicesLog.error({ err: error }, 'services_docker_status_failed')
      return []
    }
  },
)

/**
 * Get all services with their definitions and Docker status
 */
export const getServices = createServerFn()
  .inputValidator((input: Record<string, never> = {}) => input)
  .handler(async (): Promise<Array<ServiceWithStatus>> => {
    const now = Date.now()
    if (servicesCache && now - servicesCache.timestamp < servicesCacheTtlMs) {
      return servicesCache.data
    }

    // Load data in parallel
    const [services, containers, envValues, nativeProcesses] =
      await Promise.all([
        discoverServices(),
        getDockerStatus(),
        loadEnvValues(),
        loadNativeProcesses(),
      ])

    // Create a map of service name to container status
    const containerMap = new Map(
      containers.map((container) => [container.service, container]),
    )

    // Merge services with status and env values
    const data: Array<ServiceWithStatus> = []
    for (const service of services) {
      // Hide one-shot init containers from the Hub (they run once and exit successfully).
      if (service.name === 'minio-setup') {
        continue
      }

      // Update env vars with actual values from env files
      const enrichedEnvVars = service.envVars.map((ev) => ({
        ...ev,
        value: envValues[ev.name] ?? ev.value,
      }))

      // Also update port descriptions with actual values
      const enrichedPorts = service.ports.map((port) => {
        if (port.description && envValues[port.description]) {
          return {
            ...port,
            host: parseInt(envValues[port.description], 10) || port.host,
          }
        }
        return port
      })

      const firstPort = enrichedPorts[0]
      const url = firstPort ? `http://localhost:${firstPort.host}` : undefined

      const dockerStatus = containerMap.get(service.name) || null
      const native = nativeProcesses[service.name]
      const nativeStatus: DockerContainerStatus | null =
        native && isPidRunning(native.pid)
          ? {
              name: `native:${service.name}`,
              service: service.name,
              status: 'running',
              health: 'native',
              ports: undefined,
            }
          : null

      data.push({
        ...service,
        ports: enrichedPorts,
        envVars: enrichedEnvVars,
        url,
        containerStatus: nativeStatus ?? dockerStatus,
      })
    }

    servicesCache = { timestamp: now, data }
    return data
  })

export function serviceActionId(
  serviceName: string,
  action: ServiceControlAction,
): string {
  return `${serviceName}:${action}`
}

async function runServiceActionViaApi(
  serviceName: string,
  action: ServiceControlAction,
): Promise<{ success: boolean; error?: string }> {
  const result = await apiPost<ServiceActionResult>(
    '/api/observatory/actions',
    { action_id: serviceActionId(serviceName, action) },
    130000,
  )
  if (result.status === 'succeeded') {
    return { success: true }
  }
  return {
    success: false,
    error: result.message || `Service ${action} action ${result.status}`,
  }
}

async function controlService(
  serviceName: string,
  action: ServiceControlAction,
): Promise<{ success: boolean; error?: string }> {
  const startedAt = performance.now()
  servicesLog.info({ serviceName, action }, 'services_control_started')
  try {
    const result = await runServiceActionViaApi(serviceName, action)
    if (result.success) {
      servicesCache = null
      servicesLog.info(
        {
          serviceName,
          action,
          mode: 'phlo-api',
          durationMs: Math.round(performance.now() - startedAt),
        },
        'services_control_completed',
      )
    } else {
      servicesLog.warn(
        {
          serviceName,
          action,
          mode: 'phlo-api',
          error: result.error,
          durationMs: Math.round(performance.now() - startedAt),
        },
        'services_control_skipped',
      )
    }
    return result
  } catch (error) {
    servicesLog.error(
      { serviceName, action, mode: 'phlo-api', err: error },
      'services_control_failed',
    )
    return {
      success: false,
      error:
        error instanceof Error ? error.message : `Failed to ${action} service`,
    }
  }
}

/**
 * Start a service
 */
export const startService = createServerFn()
  .middleware([authMiddleware])
  .inputValidator((input: { serviceName: string }) => input)
  .handler(
    async ({
      data: { serviceName },
    }): Promise<{ success: boolean; error?: string }> => {
      return controlService(serviceName, 'start')
    },
  )

/**
 * Stop a service
 */
export const stopService = createServerFn()
  .middleware([authMiddleware])
  .inputValidator((input: { serviceName: string }) => input)
  .handler(
    async ({
      data: { serviceName },
    }): Promise<{ success: boolean; error?: string }> => {
      return controlService(serviceName, 'stop')
    },
  )

/**
 * Restart a service
 */
export const restartService = createServerFn()
  .middleware([authMiddleware])
  .inputValidator((input: { serviceName: string }) => input)
  .handler(
    async ({
      data: { serviceName },
    }): Promise<{ success: boolean; error?: string }> => {
      return controlService(serviceName, 'restart')
    },
  )
