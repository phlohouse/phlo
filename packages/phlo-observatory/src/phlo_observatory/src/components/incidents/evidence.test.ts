/** Tests the persisted evidence shapes used by incident investigations. */
import { describe, expect, it } from 'vitest'
import {
  incidentEvidence,
  incidentEvidenceNumber,
  incidentEvidenceValue,
} from './evidence'
import type { IncidentTimelineEvent } from '@/lib/data/api/incidents'

const timeline = (payload: IncidentTimelineEvent['payload']) => [
  {
    id: 'signal',
    kind: 'signal',
    actor: 'sensor',
    occurred_at: '2026-10-22T12:00:00Z',
    payload,
  },
]

describe('persisted investigation evidence', () => {
  it('reads nested sensor evidence and preserves measured values', () => {
    const events = timeline({
      evidence: {
        fixture: 'synthetic',
        observed_evidence: {
          source: 'fix-schema',
          elapsed_seconds: 7.31,
          evaluation: {
            metadataEntries: [
              { label: 'failing_rows', intValue: 37 },
              { label: 'rows_checked', intValue: 96 },
            ],
          },
        },
      },
    })
    expect(incidentEvidenceValue(events, ['branch', 'source'])).toBe(
      'fix-schema',
    )
    expect(
      incidentEvidenceNumber(incidentEvidence(events), 'elapsed_seconds'),
    ).toBe(7.31)
    expect(
      incidentEvidenceNumber(incidentEvidence(events), 'failing_rows'),
    ).toBe(37)
    expect(
      incidentEvidenceNumber(incidentEvidence(events), 'rows_checked'),
    ).toBe(96)
  })
  it('does not infer absent counts or accept string measurements', () => {
    const evidence = incidentEvidence(
      timeline({ evidence: { failing_rows: '37' } }),
    )
    expect(incidentEvidenceNumber(evidence, 'failing_rows')).toBeUndefined()
    expect(incidentEvidenceNumber(evidence, 'rows_checked')).toBeUndefined()
  })
  it('accepts sensor evidence stored directly in the event', () => {
    expect(
      incidentEvidenceValue(timeline({ branch: 'main' }), ['branch']),
    ).toBe('main')
  })
  it('keeps lifecycle updates separate from observed sensor status', () => {
    const events = timeline({ evidence: { status: 'FAILED' } })
    events.push({
      ...events[0],
      id: 'updated',
      kind: 'status_changed',
      payload: { status: 'acknowledged' },
    })
    expect(incidentEvidence(events).status).toBe('FAILED')
  })
})
