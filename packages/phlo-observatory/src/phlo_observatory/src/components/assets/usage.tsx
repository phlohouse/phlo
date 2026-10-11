import type { ApiAssetQueryUsage, ApiAssetUsage } from '@/lib/data/api/assets'

export function UsageTab({
  preview,
  queries,
}: {
  preview: ApiAssetUsage
  queries: ApiAssetQueryUsage
}) {
  return (
    <div className="grid gap-6 p-4 lg:grid-cols-2 lg:p-6">
      <section aria-label="Verified query usage">
        <h2 className="text-sm font-medium">Verified query usage</h2>
        <p className="text-sm text-muted-foreground">
          Trino completed-query inputs on {queries.nessie_ref}. Retained
          evidence is partial, not a complete access ledger.
        </p>
        {queries.status === 'unavailable' ? (
          <p role="status">
            Unavailable:{' '}
            {queries.reason === 'no_asset_relation'
              ? 'no observed table relation'
              : 'no verified query evidence'}
            .
          </p>
        ) : (
          <ul className="space-y-3 p-0 text-sm">
            {queries.items.map((item) => (
              <li
                key={`${item.source_id}:${item.query_id}`}
                className="list-none break-all"
              >
                <span className="font-mono">{item.query_id}</span>
                <br />
                <time>{item.occurred_at}</time> · {item.query_state} ·{' '}
                {item.source_id}
              </li>
            ))}
          </ul>
        )}
        {queries.next_cursor ? (
          <p className="text-sm">
            Only the latest 100 records are shown; more retained evidence
            exists.
          </p>
        ) : null}
      </section>
      <section aria-label="Preview access">
        <h2 className="text-sm font-medium">Preview access</h2>
        <p className="text-sm text-muted-foreground">
          API preview reads on {preview.nessie_ref}. These are not query usage
          and do not establish adoption.
        </p>
        {preview.status === 'unavailable' ? (
          <p role="status">Unavailable: no retained preview evidence.</p>
        ) : (
          <ul className="space-y-3 p-0 text-sm">
            {preview.items.map((item, index) => (
              <li key={`${item.observed_at}:${index}`} className="list-none">
                <time>{item.observed_at}</time> · {item.returned_row_count}{' '}
                returned rows{item.has_more ? ' · bounded preview' : ''}
              </li>
            ))}
          </ul>
        )}
        {preview.next_cursor ? (
          <p className="text-sm">
            Only the latest 100 records are shown; more retained evidence
            exists.
          </p>
        ) : null}
      </section>
    </div>
  )
}
