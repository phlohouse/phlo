/** Settings validation and observation tones without external delivery. */
import { describe, expect, it } from 'vitest'
import { serviceHealthTone } from './data/api/client'
import { emptySettings, settingsSchema } from './data/api/settings'

describe('operational settings', () => {
  it('accepts absent defaults and bounded UTC schedules', () => {
    expect(settingsSchema.safeParse(emptySettings).success).toBe(true)
    expect(
      settingsSchema.safeParse({
        ...emptySettings,
        fileSize: '4096',
        digest: 'Weekdays at 08:00',
        orphan: 'Sundays 03:00',
      }).success,
    ).toBe(true)
    for (const fileSize of ['0', '4097', '1.5', '-1', 'NaN']) {
      expect(
        settingsSchema.safeParse({ ...emptySettings, fileSize }).success,
      ).toBe(false)
    }
    expect(
      settingsSchema.safeParse({ ...emptySettings, digest: 'Daily 24:00' })
        .success,
    ).toBe(false)
    expect(
      settingsSchema.safeParse({ ...emptySettings, chat: 'not a channel' })
        .success,
    ).toBe(false)
  })
})

describe('service observations', () => {
  const now = Date.parse('2026-10-02T12:00:00Z')
  const service = {
    id: 'controlled',
    status: 'healthy' as const,
    observed_at: '2026-10-02T11:59:00Z',
    response_time_seconds: null,
  }
  it('requires recent valid evidence for a healthy tone', () => {
    expect(serviceHealthTone(service, now)).toBe('ok')
    for (const observed_at of [
      null,
      'invalid',
      '2026-10-02T11:54:59Z',
      '2026-10-02T12:00:01Z',
    ]) {
      expect(serviceHealthTone({ ...service, observed_at }, now)).toBe(
        'neutral',
      )
    }
    expect(
      serviceHealthTone(
        { ...service, observed_at: '2026-10-02T11:55:00Z' },
        now,
      ),
    ).toBe('ok')
  })
  it('keeps failures visible and inactive or unobserved services neutral', () => {
    expect(
      serviceHealthTone(
        {
          ...service,
          status: 'unhealthy',
          observed_at: '2026-10-01T12:00:00Z',
        },
        now,
      ),
    ).toBe('bad')
    expect(serviceHealthTone({ ...service, status: 'unavailable' }, now)).toBe(
      'bad',
    )
    expect(
      serviceHealthTone(
        { ...service, status: 'unavailable', observed_at: null },
        now,
      ),
    ).toBe('neutral')
    expect(serviceHealthTone({ ...service, status: 'inactive' }, now)).toBe(
      'neutral',
    )
    expect(serviceHealthTone({ ...service, status: 'degraded' }, now)).toBe(
      'warn',
    )
  })
})
