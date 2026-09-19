/** Schedule-only semantic routing for deterministic maintenance findings. */
import { defineDynamic, defineTool } from 'eve/tools'
import { z } from 'zod'
import { routeMaintenanceFindings } from '../lib/maintenance-routing'
import { isScheduleAppAuth } from '../lib/trust'

const findingSchema = z.object({
  id: z.string().min(1).max(200),
  packageName: z.string().min(1).max(200),
  installedVersion: z.string().min(1).max(100),
  fixedVersions: z.array(z.string().min(1).max(100)).max(10),
  dependencyScope: z.enum(['runtime', 'development', 'optional', 'unknown']),
  advisorySummary: z.string().min(1).max(1_000),
  affectedManifests: z.array(z.string().min(1).max(300)).max(5),
  releaseNotes: z.string().max(1_000).optional(),
})

export default defineDynamic({
  events: {
    'turn.started': (_event, ctx) => {
      if (!isScheduleAppAuth(ctx.session.auth.current)) return null
      return {
        maintenance__route_findings: defineTool({
          description: 'Route compact, machine-discovered dependency vulnerability evidence into bounded maintenance lanes with Jev. This tool does not select versions, modify files, waive findings, or deliver artifacts. Batch all findings from one audit into one call.',
          inputSchema: z.object({
            findings: z.array(findingSchema).min(1).max(20),
          }),
          async execute({ findings }) {
            return routeMaintenanceFindings(findings)
          },
        }),
      }
    },
  },
})
