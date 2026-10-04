/** Environment-scoped branch review using audited v1 read contracts. */
import { Link, createFileRoute } from '@tanstack/react-router'
import { GitBranch, GitCompare, History, ShieldAlert } from 'lucide-react'
import { useEffect, useState } from 'react'

import type {
  BranchCommitPage,
  BranchComparison,
  BranchDiff,
  BranchRead,
  BranchReference,
} from '@/observatory/api/branches'
import type { ObservatoryEnvironment } from '@/observatory/api/environment'
import {
  getBranchCommits,
  getBranchComparison,
  getBranchDiff,
  getBranchReferences,
} from '@/observatory/api/branches'
import {
  environmentChangeEvent,
  selectedEnvironment,
} from '@/observatory/api/environment'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'

export const Route = createFileRoute('/branches')({ component: Branches })

const unavailable = <T,>(message: string): BranchRead<T> => ({
  kind: 'unavailable',
  message,
})

export function Branches() {
  const [environment, setEnvironment] = useState<ObservatoryEnvironment | null>(
    () => selectedEnvironment(),
  )
  const [references, setReferences] = useState<BranchRead<
    Array<BranchReference>
  > | null>(null)
  const [selectedName, setSelectedName] = useState<string | null>(() =>
    typeof window === 'undefined'
      ? null
      : new URLSearchParams(window.location.search).get('branchId'),
  )
  const tableId =
    typeof window === 'undefined'
      ? null
      : new URLSearchParams(window.location.search).get('tableId')
  const [commits, setCommits] = useState<BranchRead<BranchCommitPage> | null>(
    null,
  )
  const [comparison, setComparison] =
    useState<BranchRead<BranchComparison> | null>(null)
  const [diff, setDiff] = useState<BranchRead<BranchDiff> | null>(null)

  useEffect(() => {
    const refresh = () => setEnvironment(selectedEnvironment())
    window.addEventListener(environmentChangeEvent(), refresh)
    return () => window.removeEventListener(environmentChangeEvent(), refresh)
  }, [])

  useEffect(() => {
    if (!environment) {
      setReferences(
        unavailable(
          'Select prod or staging to load audited branch references.',
        ),
      )
      return
    }
    let cancelled = false
    setReferences(null)
    void getBranchReferences({ data: { env: environment } }).then((result) => {
      if (!cancelled) setReferences(result)
    })
    return () => {
      cancelled = true
    }
  }, [environment])

  const referencesList = references?.kind === 'available' ? references.data : []
  const branches = referencesList.filter(
    (reference) => reference.type === 'BRANCH',
  )
  const tags = referencesList.filter((reference) => reference.type === 'TAG')
  const selectedTag = tags.find((tag) => tag.name === selectedName)
  const selected =
    branches.find((branch) => branch.name === selectedName) ??
    (selectedTag ? undefined : branches[0])
  const target = branches.find(
    (branch) => branch.protected && branch.name !== selected?.name,
  )

  function selectReference(name: string) {
    setSelectedName(name)
    const url = new URL(window.location.href)
    url.searchParams.set('branchId', name)
    window.history.replaceState(null, '', `${url.pathname}?${url.searchParams}`)
  }

  useEffect(() => {
    if (!selected || !environment) {
      setCommits(null)
      setComparison(null)
      setDiff(null)
      return
    }
    let cancelled = false
    setCommits(null)
    setComparison(
      target
        ? null
        : unavailable(
            'Comparison is unavailable: no distinct protected target reference was returned.',
          ),
    )
    setDiff(
      target
        ? null
        : unavailable(
            'Diff is unavailable: no distinct protected target reference was returned.',
          ),
    )
    void getBranchCommits({
      data: { env: environment, branch: selected.name },
    }).then((result) => {
      if (!cancelled) setCommits(result)
    })
    if (target) {
      void Promise.all([
        getBranchComparison({
          data: {
            env: environment,
            branch: selected.name,
            target: target.name,
          },
        }),
        getBranchDiff({
          data: {
            env: environment,
            branch: selected.name,
            target: target.name,
          },
        }),
      ]).then(([nextComparison, nextDiff]) => {
        if (!cancelled) {
          setComparison(nextComparison)
          setDiff(nextDiff)
        }
      })
    }
    return () => {
      cancelled = true
    }
  }, [environment, selected?.name, target?.name])

  return (
    <ObservatoryPage
      kicker="Review"
      title="Change review"
      description="Environment-scoped branch references, history, and change review from phlo-api v1."
    >
      <section className="phlo-observatory-surface-grid phlo-observatory-branch-grid">
        <div className="phlo-observatory-branch-main">
          {tableId && (
            <div className="phlo-observatory-panel-footer">
              Table context {tableId} has no branch-table detail in v1.{' '}
              <Link to="/tables" search={{ tableId }}>
                Open the table
              </Link>
            </div>
          )}
          <div className="phlo-observatory-list-surface">
            <div className="phlo-observatory-browser-toolbar">
              <span>
                <GitBranch className="size-4" />
                Change reviews
              </span>
            </div>
            {references === null && <p>Loading audited branch references…</p>}
            {references?.kind === 'unavailable' && (
              <Unavailable message={references.message} />
            )}
            {referencesList.map((branch) => (
              <button
                className="phlo-observatory-row phlo-observatory-select-row"
                data-active={branch.name === selected?.name}
                key={branch.name}
                onClick={() => selectReference(branch.name)}
                type="button"
              >
                <div className="phlo-observatory-row-main">
                  <div className="phlo-observatory-row-title">
                    {branch.name}
                  </div>
                  <div className="phlo-observatory-row-meta">{branch.hash}</div>
                </div>
                <span className="phlo-observatory-pill">
                  {branch.type === 'TAG'
                    ? 'tag'
                    : branch.protected
                      ? 'protected branch'
                      : 'branch'}
                </span>
              </button>
            ))}
            {references?.kind === 'available' && referencesList.length === 0 ? (
              <p>No branch references or tags were returned.</p>
            ) : null}
          </div>
          {selectedTag ? (
            <section className="phlo-observatory-branch-summary">
              <div className="phlo-observatory-branch-summary-copy">
                <div className="phlo-observatory-inspector-label">
                  Selected tag
                </div>
                <h2>{selectedTag.name}</h2>
                <p>
                  Tag hash {selectedTag.hash}. Tags are read-only references.
                </p>
              </div>
            </section>
          ) : null}
          {selected && (
            <>
              <section className="phlo-observatory-branch-summary">
                <div className="phlo-observatory-branch-summary-copy">
                  <div className="phlo-observatory-inspector-label">
                    Selected branch
                  </div>
                  <h2>{selected.name}</h2>
                  <p>
                    Head {selected.hash}.{' '}
                    {target
                      ? `Compared with ${target.name}.`
                      : 'No protected comparison target is available.'}
                  </p>
                </div>
                <dl className="phlo-observatory-branch-facts">
                  <Fact
                    label="Ahead"
                    value={comparisonValue(comparison, 'ahead')}
                  />
                  <Fact
                    label="Behind"
                    value={comparisonValue(comparison, 'behind')}
                  />
                  <Fact
                    label="Changes"
                    value={
                      diff?.kind === 'available'
                        ? String(diff.data.items.length)
                        : 'Unavailable'
                    }
                  />
                </dl>
              </section>
              <section className="phlo-observatory-branch-review">
                <h3>
                  <GitCompare className="size-4" /> Diff
                </h3>
                <DiffView result={diff} />
              </section>
              <section className="phlo-observatory-detail-list">
                <h3>
                  <History className="size-4" /> History
                </h3>
                <HistoryView result={commits} />
              </section>
              <section
                className="phlo-observatory-branch-readiness"
                data-state="unknown"
              >
                <div>
                  <div className="phlo-observatory-inspector-label">
                    Lifecycle and mutations
                  </div>
                  <h3>Unavailable in this screen</h3>
                  <p>
                    Creating, rebasing, merging, and running checks require an
                    audited, idempotent mutation request; merge additionally
                    requires a bound signature. This replacement screen does not
                    send legacy mutations or infer success.
                  </p>
                </div>
                <ShieldAlert className="size-5" />
              </section>
            </>
          )}
        </div>
      </section>
    </ObservatoryPage>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}
function comparisonValue(
  result: BranchRead<BranchComparison> | null,
  key: 'ahead' | 'behind',
): string {
  if (result?.kind !== 'available' || result.data.status !== 'compared')
    return 'Unavailable'
  const value = result.data[key]
  return value === null ? 'Unavailable' : String(value)
}
function Unavailable({ message }: { message: string }) {
  return (
    <div className="phlo-observatory-panel-footer">Unavailable: {message}</div>
  )
}
function DiffView({ result }: { result: BranchRead<BranchDiff> | null }) {
  if (result === null) return <p>Loading diff…</p>
  if (result.kind === 'unavailable')
    return <Unavailable message={result.message} />
  if (result.data.items.length === 0)
    return <p>No changes reported by the v1 diff contract.</p>
  return (
    <div className="phlo-observatory-detail-list">
      {result.data.items.map((change) => (
        <div className="phlo-observatory-mini-row" key={change.key}>
          <span>{change.key}</span>
          <small>{change.status}</small>
        </div>
      ))}
      {result.data.truncated && <p>Diff is truncated by the backend.</p>}
    </div>
  )
}
function HistoryView({
  result,
}: {
  result: BranchRead<BranchCommitPage> | null
}) {
  if (result === null) return <p>Loading history…</p>
  if (result.kind === 'unavailable')
    return <Unavailable message={result.message} />
  if (result.data.items.length === 0)
    return <p>No commits reported by the v1 history contract.</p>
  return (
    <>
      {result.data.items.map((commit) => (
        <div className="phlo-observatory-mini-row" key={commit.hash}>
          <span>{commit.message ?? commit.hash}</span>
          <small>
            {[commit.author, commit.committed_at, commit.hash]
              .filter(Boolean)
              .join(' · ')}
          </small>
        </div>
      ))}
      {result.data.next_cursor && (
        <p>
          Additional history is available but pagination is not yet supported in
          this screen.
        </p>
      )}
    </>
  )
}
