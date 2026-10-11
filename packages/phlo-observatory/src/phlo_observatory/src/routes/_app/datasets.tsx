import { createFileRoute } from '@tanstack/react-router'
import { getDatasetInventory } from '@/lib/data/api/datasets'
import { PageHeader } from '@/components/phlo/page'

export const Route = createFileRoute('/_app/datasets')({
  loader: async () => {
    try {
      return { kind: 'inventory' as const, data: await getDatasetInventory() }
    } catch (error) {
      return {
        kind: 'unavailable' as const,
        message:
          error instanceof Error
            ? error.message
            : 'Dataset authority unavailable.',
      }
    }
  },
  head: () => ({ meta: [{ title: 'Governed Datasets · phlo' }] }),
  component: DatasetsPage,
})

function DatasetsPage() {
  const result = Route.useLoaderData()
  return (
    <>
      <PageHeader
        title="Governed Datasets"
        crumbs={[{ label: 'Assets', to: '/assets' }]}
      />
      <div className="min-h-0 overflow-y-auto p-4 lg:p-7">
        <p className="text-sm text-muted-foreground">
          Project-scoped declarations and canonical core Dataset authority. The
          environment selector does not remap governance state. Materialisation
          does not establish publication. Stored candidates outside the
          declaration inventory are not listed here.
        </p>
        {result.kind === 'unavailable' ? (
          <p role="alert">Inventory unavailable. {result.message}</p>
        ) : (
          <>
            {!result.data.items.length ? (
              <p>No governed Dataset declarations.</p>
            ) : null}
            {result.data.truncated ? (
              <p role="status">
                Only the first 500 declarations are displayed.
              </p>
            ) : null}
            <div className="grid gap-4 lg:grid-cols-2">
              {result.data.items.map((dataset) => (
                <article
                  key={dataset.dataset_id}
                  className="min-w-0 rounded border border-line p-4"
                >
                  <h2 className="break-all font-mono text-sm font-medium">
                    {dataset.dataset_id}
                  </h2>
                  <dl className="space-y-2 text-sm">
                    <div>
                      <dt>Owner</dt>
                      <dd>{dataset.owner ?? 'Not recorded'}</dd>
                    </div>
                    <div>
                      <dt>Publication state</dt>
                      <dd>{dataset.publication_state ?? 'Not recorded'}</dd>
                    </div>
                    <div>
                      <dt>Workflow / approval</dt>
                      <dd>
                        {dataset.workflow_state ?? 'Not recorded'} /{' '}
                        {dataset.approval_state ?? 'Not recorded'}
                      </dd>
                    </div>
                    <div>
                      <dt>Classifications</dt>
                      <dd>
                        {dataset.classifications.join(', ') || 'Not declared'}
                      </dd>
                    </div>
                    <div>
                      <dt>Readiness for {dataset.readiness.action}</dt>
                      <dd>{dataset.readiness.ready ? 'Ready' : 'Blocked'}</dd>
                    </div>
                  </dl>
                  <ul className="text-sm">
                    {dataset.readiness.reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                  <details>
                    <summary className="cursor-pointer text-sm">
                      Canonical controls and evidence
                    </summary>
                    <pre className="overflow-x-auto text-xs">
                      {JSON.stringify(dataset, null, 2)}
                    </pre>
                  </details>
                </article>
              ))}
            </div>
          </>
        )}
      </div>
    </>
  )
}
