/** Verifies staging check evidence belongs to the current candidate. */
import { describe, expect, it } from 'vitest'
import { stagingChecksPassed } from './staging'
import type { StagingCandidate, StagingChecks } from './staging'

const candidateId = 'candidate-current'
const readiness: Array<
  Pick<StagingCandidate['check_readiness'][number], 'status'>
> = [{ status: 'ready' }]
const unreadyReadiness: Array<
  Pick<StagingCandidate['check_readiness'][number], 'status'>
> = [{ status: 'missing_evidence' }]

const checks = (
  candidate_id: string,
  passed: boolean,
): Pick<StagingChecks, 'candidate_id' | 'passed'> => ({ candidate_id, passed })

describe('stagingChecksPassed', () => {
  it('uses check evidence only when it belongs to the current candidate', () => {
    expect(
      stagingChecksPassed(candidateId, readiness, checks(candidateId, false)),
    ).toBe(false)
    expect(
      stagingChecksPassed(
        candidateId,
        unreadyReadiness,
        checks('candidate-stale', true),
      ),
    ).toBe(false)
  })

  it('uses current readiness when no matching check result exists', () => {
    expect(stagingChecksPassed(candidateId, readiness, undefined)).toBe(true)
    expect(
      stagingChecksPassed(
        candidateId,
        unreadyReadiness,
        checks('candidate-stale', false),
      ),
    ).toBe(false)
  })
})
