/**
 * Tests service-status helpers: container state mapping and Docker
 * status-line fallbacks.
 */
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import {
  getEnvPath,
  loadEnvValues,
  parseContainerStateStatus,
  parseDockerStatusLines,
  serviceActionId,
  shouldFallbackToCliDiscovery,
} from '@/server/services.server'

describe('services.server helpers', () => {
  describe('parseContainerStateStatus', () => {
    it('parses running/unhealthy/starting/stopped/unknown states deterministically', () => {
      expect(
        parseContainerStateStatus({ State: 'running', Health: 'healthy' }),
      ).toBe('running')
      expect(
        parseContainerStateStatus({ State: 'running', Health: 'unhealthy' }),
      ).toBe('unhealthy')
      expect(parseContainerStateStatus({ State: 'created' })).toBe('starting')
      expect(parseContainerStateStatus({ State: 'exited (0)' })).toBe('stopped')
      expect(parseContainerStateStatus({ State: '' })).toBe('unknown')
    })

    it('falls back to docker Status text when State is missing', () => {
      expect(parseContainerStateStatus({ Status: 'Up 10 seconds' })).toBe(
        'running',
      )
      expect(parseContainerStateStatus({ Status: 'running' })).toBe('running')
    })
  })

  describe('parseDockerStatusLines', () => {
    const alphaStopped = JSON.stringify({
      Names: 'alpha-dagster-1',
      Labels:
        'com.docker.compose.project=alpha,com.docker.compose.service=dagster-webserver',
      State: 'exited (0)',
      Health: '',
      Ports: '0.0.0.0:3000->3000/tcp',
    })

    const alphaRunning = JSON.stringify({
      Names: 'alpha-dagster-2',
      Labels:
        'com.docker.compose.project=alpha,com.docker.compose.service=dagster-webserver',
      State: 'running',
      Health: 'healthy',
      Ports: '0.0.0.0:3000->3000/tcp',
    })

    const betaRunning = JSON.stringify({
      Names: 'beta-minio-1',
      Labels:
        'com.docker.compose.project=beta,com.docker.compose.service=minio',
      State: 'running',
      Health: 'healthy',
      Ports: '0.0.0.0:9000->9000/tcp',
    })

    it('scopes by compose project and keeps highest-priority status per service', () => {
      const statuses = parseDockerStatusLines(
        `${alphaStopped}\n${alphaRunning}\n${betaRunning}`,
        'alpha',
      )

      expect(statuses).toHaveLength(1)
      expect(statuses[0]).toMatchObject({
        service: 'dagster-webserver',
        status: 'running',
        name: 'alpha-dagster-2',
      })
    })

    it('includes all compose projects when project scope is unset and skips invalid lines', () => {
      const statuses = parseDockerStatusLines(
        `${alphaRunning}\nnot-json\n${betaRunning}`,
        null,
      )

      expect(statuses.map((status) => status.service).sort()).toEqual([
        'dagster-webserver',
        'minio',
      ])
    })
  })

  describe('shouldFallbackToCliDiscovery', () => {
    it('falls back only when yaml discovery is active and returns zero services', () => {
      expect(shouldFallbackToCliDiscovery(false, 0)).toBe(true)
      expect(shouldFallbackToCliDiscovery(false, 2)).toBe(false)
      expect(shouldFallbackToCliDiscovery(true, 0)).toBe(false)
    })
  })

  describe('serviceActionId', () => {
    it('matches the phlo-api v2 service action contract', () => {
      expect(serviceActionId('postgres', 'restart')).toBe('postgres:restart')
    })
  })
})

describe('project environment layouts', () => {
  it('discovers shared defaults and loads secrets with final precedence', async () => {
    const project = await mkdtemp(join(tmpdir(), 'phlo-env-'))
    try {
      const root = join(project, '.phlo')
      await mkdir(join(root, 'overrides'), { recursive: true })
      await mkdir(join(root, 'secrets'))
      await writeFile(join(root, '.env'), 'PORT=1\nLEGACY=yes\n')
      await writeFile(join(root, '.env.local'), 'PORT=2\n')
      await writeFile(join(root, 'overrides', '.env'), 'PORT=3\nTEAM=yes\n')
      await writeFile(join(root, 'secrets', '.env'), 'PORT=4\n')
      expect(getEnvPath('', project)).toBe(join(root, 'overrides', '.env'))
      expect(await loadEnvValues(getEnvPath('', project))).toEqual({
        PORT: '4',
        LEGACY: 'yes',
        TEAM: 'yes',
      })
      expect(await loadEnvValues(join(root, '.env'))).toEqual({
        PORT: '4',
        LEGACY: 'yes',
        TEAM: 'yes',
      })
    } finally {
      await rm(project, { recursive: true, force: true })
    }
  })

  it('retains explicit custom env paths and adjacent local overrides', async () => {
    const project = await mkdtemp(join(tmpdir(), 'phlo-env-'))
    try {
      const custom = join(project, 'custom.env')
      await writeFile(custom, 'PORT=1\n')
      await writeFile(`${custom}.local`, 'PORT=2\n')
      expect(getEnvPath(custom, project)).toBe(custom)
      expect(await loadEnvValues(custom)).toEqual({ PORT: '2' })
    } finally {
      await rm(project, { recursive: true, force: true })
    }
  })
})
