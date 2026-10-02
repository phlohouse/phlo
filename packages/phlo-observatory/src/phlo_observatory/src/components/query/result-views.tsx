/** Presents query results as a data grid or execution plan. */
import type { QueryResult } from '@/lib/data/api/query'

const th =
  'sticky top-0 z-10 h-[34px] border-r border-b border-line bg-raised px-3 text-left font-sans text-xs font-normal whitespace-nowrap text-muted-foreground'
const td =
  'h-8 max-w-[420px] overflow-hidden text-ellipsis border-r border-b border-line-soft px-3 whitespace-nowrap text-text-2'
const display = (value: unknown) =>
  value === null
    ? 'NULL'
    : typeof value === 'object'
      ? JSON.stringify(value)
      : String(value)

export function ResultsGrid({ result }: { result: QueryResult }) {
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <table
        className="w-full min-w-max border-separate border-spacing-0 font-mono text-[12.5px]"
        aria-label="Query results"
      >
        <thead>
          <tr>
            <th scope="col" className={`${th} text-right`}>
              #
            </th>
            {result.columns.map((column) => (
              <th key={column.name} scope="col" className={th}>
                {column.name}{' '}
                <span className="text-[10.5px] text-faint">
                  {column.type ?? 'unknown'}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row, index) => (
            <tr key={index} className="hover:bg-raised">
              <td className={`${td} text-right text-faint`}>{index + 1}</td>
              {result.columns.map((column) => (
                <td
                  key={column.name}
                  className={td}
                  title={display(row[column.name])}
                >
                  {display(row[column.name])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function PlanView({ result }: { result: QueryResult }) {
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <pre className="m-0 px-4 py-4 font-mono text-[12.5px] leading-6 whitespace-pre-wrap text-text-2">
        {result.rows
          .map((row) =>
            result.columns
              .map((column) => display(row[column.name]))
              .join('\n'),
          )
          .join('\n')}
      </pre>
    </div>
  )
}
