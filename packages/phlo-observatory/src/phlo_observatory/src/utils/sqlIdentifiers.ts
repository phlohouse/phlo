/** Quote an identifier with double quotes, escaping embedded quotes by
 * doubling them (ANSI/Trino convention). */
export function quoteIdentifier(identifier: string): string {
  const escaped = identifier.replaceAll('"', '""')
  return `"${escaped}"`
}
