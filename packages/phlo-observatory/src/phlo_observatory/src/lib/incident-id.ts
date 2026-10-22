/** A compact display prefix; links and mutations always retain the full ID. */
export function shortIncidentId(id: string): string {
  return /^[a-f0-9-]{24,}$/i.test(id) ? id.replaceAll('-', '').slice(0, 8) : id
}
