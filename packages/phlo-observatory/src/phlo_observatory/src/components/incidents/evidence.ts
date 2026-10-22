/** Reads persisted incident evidence without interpreting missing values as facts. */
import type { IncidentTimelineEvent } from '@/lib/data/api/incidents'

type Evidence = Record<string, unknown>
export function evidenceObject(value: unknown): Evidence {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Evidence)
    : {}
}

export function incidentEvidence(timeline: Array<IncidentTimelineEvent>) {
  return timeline.reduce<Evidence>((result, event) => {
    if (event.kind !== 'signal' && !event.payload.evidence) return result
    const evidence = evidenceObject(event.payload.evidence)
    return {
      ...result,
      ...event.payload,
      ...evidence,
      ...evidenceObject(evidence.observed_evidence),
    }
  }, {})
}

export function incidentEvidenceValue(
  timeline: Array<IncidentTimelineEvent>,
  keys: Array<string>,
) {
  const evidence = incidentEvidence(timeline)
  return keys
    .map((key) => evidence[key])
    .find(
      (value): value is string => typeof value === 'string' && !!value.trim(),
    )
}

export function incidentEvidenceNumber(evidence: Evidence, name: string) {
  const direct = evidence[name]
  if (typeof direct === 'number' && Number.isFinite(direct)) return direct
  const entries = evidenceObject(evidence.evaluation).metadataEntries
  if (!Array.isArray(entries)) return undefined
  const entry = entries.map(evidenceObject).find((item) => item.label === name)
  const value = entry?.intValue ?? entry?.floatValue
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

export function incidentDuration(seconds: number) {
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  return `${Math.floor(seconds / 60)} m ${Math.round(seconds % 60)} s`
}
