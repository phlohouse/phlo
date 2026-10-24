/** Notification server adapters preserve environment and never accept a caller-selected subject. */
import { beforeEach, expect, it, vi } from 'vitest'
import { z } from 'zod'
import { getIncidentSubscription, markNotificationsRead } from './notifications'
import { phloApi } from './client'

vi.mock('@tanstack/react-start', () => ({
  createServerFn: () => {
    let schema: z.ZodType
    const builder = {
      inputValidator(value: z.ZodType) {
        schema = value
        return builder
      },
      handler(handler: (args: { data: unknown }) => unknown) {
        return (args: { data: unknown }) =>
          handler({ data: schema.parse(args.data) })
      },
    }
    return builder
  },
}))
vi.mock('./client', () => ({ environmentSchema: z.string(), phloApi: vi.fn() }))
beforeEach(() => vi.resetAllMocks())
it('sends an explicit JSON read intent for only the chosen event identities', async () => {
  vi.mocked(phloApi).mockResolvedValue({ event_ids: ['event-1'] })
  await markNotificationsRead({
    data: { env: 'sandbox', eventIds: ['event-1'] },
  })
  expect(phloApi).toHaveBeenCalledWith(
    '/api/v1/notifications/read-state?env=sandbox',
    expect.anything(),
    {
      env: 'sandbox',
      method: 'PUT',
      headers: { 'x-phlo-intent': 'notifications.mark-read' },
      body: { event_ids: ['event-1'] },
    },
  )
})
it('loads current-actor subscription state through the incident-scoped GET', async () => {
  vi.mocked(phloApi).mockResolvedValue({
    incident_id: 'incident-1',
    subscribed: true,
  })
  await getIncidentSubscription({ data: { env: 'sandbox', id: 'incident-1' } })
  expect(phloApi).toHaveBeenCalledWith(
    '/api/v1/incidents/incident-1/subscriptions?env=sandbox',
    expect.anything(),
    { env: 'sandbox' },
  )
})
