/** Defines the branch workspace for comparisons, checks, and lifecycle actions. */
import * as React from 'react'
import { Link, createFileRoute, useRouter } from '@tanstack/react-router'
import { GitMergeIcon, PlusIcon, RefreshCwIcon, Trash2Icon } from 'lucide-react'
import { z } from 'zod'
import type { Env } from '@/lib/data/types'
import type { BranchAction, BranchCheck } from '@/lib/data/api/branches'
import {
  createBranch,
  deleteBranch,
  getBranchDetail,
  getBranchesPage,
  rebaseBranch,
  runBranchChecks,
  signAndMerge,
  trialMerge,
} from '@/lib/data/api/branches'
import { Eyebrow, PageHeader } from '@/components/phlo/page'
import { EmptyState } from '@/components/phlo/states'
import { Mono } from '@/components/phlo/status'
import { MergeDialog } from '@/components/branches/merge-dialog'
import { NewBranchDialog } from '@/components/branches/new-branch-dialog'
import { BranchGraph } from '@/components/branches/branch-graph'
import { WapRuns } from '@/components/branches/wap-runs'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'

const searchSchema = z.object({
  dialog: z.enum(['new-branch', 'merge']).optional(),
  branch: z.string().optional(),
  view: z.enum(['references', 'wap']).default('references'),
})
export const Route = createFileRoute('/_app/branches')({
  validateSearch: searchSchema,
  loaderDeps: ({ search }) => ({ env: search.env, view: search.view }),
  loader: ({ deps }): Promise<BranchPage> =>
    deps.view === 'wap'
      ? Promise.resolve({ env: deps.env, branches: [], tags: [] })
      : getBranchesPage({ data: { env: deps.env } }),
  head: () => ({ meta: [{ title: 'Branches · phlo' }] }),
  component: BranchesPage,
})

function operationKey(env: string, action: string, intent: string) {
  const storageKey = `phlo:branch:${env}:${action}:${intent}`
  const existing = sessionStorage.getItem(storageKey)
  if (existing) return existing
  const key = crypto.randomUUID()
  sessionStorage.setItem(storageKey, key)
  return key
}

type BranchPage = Awaited<ReturnType<typeof getBranchesPage>>
type Branch = BranchPage['branches'][number]
type BranchDetail = Awaited<ReturnType<typeof getBranchDetail>>

function BranchReferences({
  data,
  env,
  selected,
}: {
  data: BranchPage
  env: Env
  selected?: Branch
}) {
  return (
    <section
      aria-label="References"
      className="shrink-0 border-b border-line p-3 lg:w-[340px] lg:overflow-y-auto lg:border-r lg:border-b-0"
    >
      <details className="px-4 pt-1 pb-3 text-sm text-muted-foreground">
        <summary className="cursor-pointer hover:text-foreground">
          Which branches are shown?
        </summary>
        <p className="mt-2 mb-0">
          The configured {env} reference and branches named {env}-*. Open the
          WAP tab for temporary staging references and their run observations.
        </p>
      </details>
      {data.branches.map((branch) => (
        <Link
          key={branch.name}
          to="/branches"
          search={{ env, branch: branch.name }}
          className={cn(
            'flex min-w-0 flex-col gap-1 rounded-[10px] px-4 py-3 text-foreground hover:bg-sunken',
            selected?.name === branch.name && 'bg-soft',
          )}
        >
          <span className="flex gap-2">
            <Mono className="min-w-0 break-all">{branch.name}</Mono>
            {branch.protected ? (
              <Badge variant="outline">protected</Badge>
            ) : null}
          </span>
          <Mono
            className="truncate text-xs text-muted-foreground"
            title={branch.hash}
          >
            {branch.hash.slice(0, 12)}
          </Mono>
        </Link>
      ))}
      <Eyebrow className="px-4 pt-5">Tags</Eyebrow>
      {data.tags.map((tag) => (
        <div key={tag.name} className="flex flex-col px-4 py-2">
          <Mono>{tag.name}</Mono>
          <Mono className="text-xs text-muted-foreground">{tag.hash}</Mono>
        </div>
      ))}
    </section>
  )
}

function BranchEvidence({
  detail,
  target,
  selected,
}: {
  detail?: BranchDetail
  target: Branch
  selected: Branch
}) {
  if (!detail)
    return <p className="text-sm text-muted-foreground">Loading branch data…</p>
  const baseIndex = detail.commits.items.findIndex(
    (commit) => commit.hash === detail.comparison.merge_base,
  )
  const graphKnown =
    target.name === 'main' &&
    baseIndex >= 0 &&
    detail.comparison.merge_base !== null &&
    detail.comparison.behind !== null
  return (
    <div>
      <div className="pt-5 pb-2">
        {graphKnown &&
        detail.comparison.merge_base !== null &&
        detail.comparison.behind !== null ? (
          <BranchGraph
            name={selected.name}
            graph={{
              base: detail.comparison.merge_base,
              commits: detail.commits.items
                .slice(0, baseIndex)
                .map((commit) => commit.hash)
                .toReversed(),
              behind: detail.comparison.behind,
              mergeable: false,
            }}
            commits={detail.commits.items.map((commit) => ({
              id: commit.hash,
              message: commit.message ?? 'No commit message',
              who: commit.author ?? commit.committer ?? 'Unknown author',
              ago: commit.committed_at ?? 'Unknown time',
            }))}
          />
        ) : (
          <div className="flex h-[132px] items-center justify-center rounded-lg border border-dashed border-line text-[13px] text-muted-foreground">
            Branch graph unavailable: no verified fork and comparison history.
          </div>
        )}
      </div>
      <div className="grid gap-7 pt-3 lg:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-3">
          <Eyebrow>Comparison with {target.name}</Eyebrow>
          <p className="text-sm">
            Ahead: {detail.comparison.ahead ?? 'unknown'} · Behind:{' '}
            {detail.comparison.behind ?? 'unknown'} · Merge base:{' '}
            <Mono>
              {(detail.comparison.merge_base ?? 'unknown').slice(0, 12)}
            </Mono>
          </p>
          <Eyebrow className="mt-6">
            Changes{detail.diff.truncated ? ' (first 500)' : ''}
          </Eyebrow>
          {detail.diff.items.length ? (
            <ul className="m-0 list-none overflow-hidden rounded-[10px] border border-border-card p-0">
              {detail.diff.items.map((change) => (
                <li
                  key={change.key}
                  className="border-b border-line-soft px-3.5 py-3 text-sm last:border-0"
                >
                  <Badge variant="outline">{change.status}</Badge>{' '}
                  <Mono>{change.key}</Mono>
                  <div className="text-xs text-muted-foreground">
                    {change.from_content_id ?? 'none'} →{' '}
                    {change.to_content_id ?? 'none'}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="No table changes" className="mt-2">
              The API returned no changes against {target.name}.
            </EmptyState>
          )}
        </div>
        <div className="min-w-0">
          <Eyebrow>
            Commit history{detail.commits.next_cursor ? ' (first 100)' : ''}
          </Eyebrow>
          {detail.commits.items.length ? (
            <div className="mt-2 overflow-hidden rounded-[10px] border border-border-card">
              {detail.commits.items.map((commit) => (
                <div
                  key={commit.hash}
                  className="grid min-w-0 gap-1 border-b border-line-soft px-3.5 py-3 last:border-0 sm:grid-cols-[112px_minmax(0,1fr)] sm:gap-x-3"
                >
                  <Mono className="block truncate text-xs text-branch">
                    {commit.hash.slice(0, 12)}
                  </Mono>
                  <div className="min-w-0 break-words text-sm">
                    {commit.message ?? 'No commit message'}
                  </div>
                  <div className="break-all text-xs text-muted-foreground sm:col-start-2">
                    {commit.author ?? commit.committer ?? 'Unknown author'} ·{' '}
                    {commit.committed_at ?? 'Unknown time'} · parents:{' '}
                    {commit.parent_hashes.length
                      ? commit.parent_hashes.join(', ')
                      : 'none'}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="No commit history" className="mt-2">
              No commits were returned.
            </EmptyState>
          )}
        </div>
      </div>
    </div>
  )
}

function BranchActions({
  selected,
  target,
  busy,
  env,
  run,
  refresh,
}: {
  selected: Branch
  target: Branch
  busy: boolean
  env: Env
  run: (
    work: () => Promise<BranchAction | void>,
    done?: () => void,
  ) => Promise<void>
  refresh: () => Promise<void>
}) {
  if (selected.protected) return null
  return (
    <>
      <Button
        variant="outline"
        disabled={busy}
        onClick={() =>
          window.confirm(
            `Rebase ${selected.name} at ${selected.hash} onto ${target.name} at ${target.hash}?`,
          ) &&
          run(
            () =>
              rebaseBranch({
                data: {
                  env,
                  branch: selected.name,
                  target: target.name,
                  sourceHash: selected.hash,
                  targetHash: target.hash,
                  confirmed: true,
                  idempotencyKey: operationKey(
                    env,
                    'rebase',
                    `${selected.name}@${selected.hash}<-${target.name}@${target.hash}`,
                  ),
                },
              }),
            refresh,
          )
        }
      >
        <RefreshCwIcon /> Rebase
      </Button>
      <Link
        to="/branches"
        search={{ env, branch: selected.name, dialog: 'merge' }}
        className={buttonVariants()}
      >
        <GitMergeIcon /> Merge
      </Link>
      <Button
        variant="destructive"
        disabled={busy}
        onClick={() =>
          window.confirm(`Delete ${selected.name} at ${selected.hash}?`) &&
          run(
            () =>
              deleteBranch({
                data: {
                  env,
                  branch: selected.name,
                  expectedHash: selected.hash,
                  confirmed: true,
                  idempotencyKey: operationKey(
                    env,
                    'delete',
                    `${selected.name}@${selected.hash}`,
                  ),
                },
              }),
            refresh,
          )
        }
      >
        <Trash2Icon /> Delete
      </Button>
    </>
  )
}

function BranchHeader({
  data,
  view,
}: {
  data: BranchPage
  view: 'references' | 'wap'
}) {
  return (
    <PageHeader
      title="Branches"
      meta={
        view === 'wap'
          ? `${data.env} · WAP staging observations`
          : `Nessie catalog · ${data.branches.length} branches, ${data.tags.length} tags`
      }
      actions={
        view === 'wap' ? undefined : (
          <Link
            to="/branches"
            search={{ env: data.env, dialog: 'new-branch' }}
            className={cn(
              buttonVariants({ variant: 'outline' }),
              'h-10 lg:h-8',
            )}
          >
            <PlusIcon /> New branch
          </Link>
        )
      }
    />
  )
}

function BranchesPage() {
  const data = Route.useLoaderData(),
    { env, dialog, view, branch: selectedName } = Route.useSearch(),
    navigate = Route.useNavigate(),
    router = useRouter()
  const target = data.branches.find((branch) => branch.protected)
  const selected =
    data.branches.find((branch) => branch.name === selectedName) ??
    target ??
    data.branches[0]
  const [detail, setDetail] =
    React.useState<Awaited<ReturnType<typeof getBranchDetail>>>()
  const [busy, setBusy] = React.useState(false),
    [error, setError] = React.useState<string>(),
    [checks, setChecks] = React.useState<Array<BranchCheck>>(),
    [trial, setTrial] = React.useState<BranchAction>()
  React.useEffect(() => {
    let current = true
    setDetail(undefined)
    setChecks(undefined)
    setTrial(undefined)
    setError(undefined)
    if (selected && target)
      getBranchDetail({
        data: { env, branch: selected.name, target: target.name },
      })
        .then((value) => {
          if (current) setDetail(value)
        })
        .catch((caught) => {
          if (current)
            setError(
              caught instanceof Error
                ? caught.message
                : 'Could not load branch.',
            )
        })
    return () => {
      current = false
    }
  }, [env, selected?.name, selected?.hash, target?.name, target?.hash])
  const close = () =>
    navigate({
      search: (current) => ({ ...current, dialog: undefined }),
      replace: true,
    })
  const run = async (
    work: () => Promise<BranchAction | void>,
    done?: () => void,
  ) => {
    if (busy) return
    setBusy(true)
    setError(undefined)
    try {
      const result = await work()
      if (result && result.status !== 'succeeded')
        throw new Error(
          `Branch operation reported ${result.status}. Reload branch evidence before retrying.`,
        )
      await done?.()
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : 'Branch operation failed.',
      )
    } finally {
      setBusy(false)
    }
  }
  const refresh = async () => {
    close()
    await router.invalidate()
  }
  const checkBranch = async () => {
    if (!selected) return
    setChecks(undefined)
    setTrial(undefined)
    const intent = `${selected.name}@${selected.hash}`
    const result = await runBranchChecks({
      data: {
        env,
        branch: selected.name,
        expectedHash: selected.hash,
        confirmed: true,
        idempotencyKey: operationKey(env, 'checks', intent),
      },
    })
    sessionStorage.removeItem(`phlo:branch:${env}:checks:${intent}`)
    setChecks(result.items)
  }

  return (
    <>
      <BranchHeader data={data} view={view} />
      <Tabs
        value={view}
        className="flex-1"
        onValueChange={(value) => {
          if (value === 'references' || value === 'wap')
            navigate({
              search: (current) => ({
                ...current,
                view: value,
                dialog: undefined,
              }),
            })
        }}
      >
        <TabsList aria-label="Branch views">
          <TabsTrigger value="references">References</TabsTrigger>
          <TabsTrigger value="wap">WAP</TabsTrigger>
        </TabsList>
        <TabsContent value="wap" className="overflow-y-auto">
          <WapRuns key={env} env={env} />
        </TabsContent>
        <TabsContent value="references" className="flex min-h-0 flex-col">
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
            <BranchReferences data={data} env={env} selected={selected} />
            {!selected || !target ? (
              <EmptyState title="No configured branches" className="m-6">
                No protected environment ref was returned by the API.
              </EmptyState>
            ) : (
              <section className="min-w-0 flex-1 overflow-y-auto">
                <div className="flex flex-wrap items-start gap-3 border-b border-line px-4 pt-5 pb-4 lg:px-7 lg:pt-[22px] lg:pb-[18px]">
                  <div className="mr-auto min-w-0">
                    <h2 className="m-0 break-all font-mono text-xl">
                      {selected.name}
                    </h2>
                    <Mono
                      className="block max-w-full truncate text-xs text-muted-foreground"
                      title={selected.hash}
                    >
                      {selected.hash.slice(0, 12)}
                    </Mono>
                    <div className="mt-1 text-[13px] text-muted-foreground">
                      {selected.protected
                        ? 'Protected environment reference'
                        : `Compared with ${target.name}`}{' '}
                      · exact revision pinned
                    </div>
                  </div>
                  <BranchActions
                    selected={selected}
                    target={target}
                    busy={busy}
                    env={env}
                    run={run}
                    refresh={refresh}
                  />
                </div>
                <div className="px-4 pb-6 lg:px-7">
                  <BranchEvidence
                    detail={detail}
                    target={target}
                    selected={selected}
                  />
                </div>
                {error && dialog !== 'merge' ? (
                  <p className="text-sm text-bad-text" role="alert">
                    {error}
                  </p>
                ) : null}
              </section>
            )}
          </div>
        </TabsContent>
      </Tabs>
      <NewBranchDialog
        open={dialog === 'new-branch'}
        env={env}
        refs={[...data.branches, ...data.tags]}
        busy={busy}
        error={error}
        onClose={close}
        onCreate={(name, fromRef) =>
          run(
            () =>
              createBranch({
                data: {
                  env,
                  name,
                  fromRef,
                  confirmed: true,
                  idempotencyKey: operationKey(
                    env,
                    'create',
                    `${name}<-${fromRef}`,
                  ),
                },
              }),
            refresh,
          )
        }
      />
      {selected && target && !selected.protected ? (
        <MergeDialog
          key={`${env}:${selected.name}:${selected.hash}:${target.name}:${target.hash}`}
          open={dialog === 'merge'}
          branch={selected}
          target={target}
          busy={busy}
          error={error}
          checks={checks}
          trial={trial}
          onClose={close}
          onMessageChange={() => {
            setChecks(undefined)
            setTrial(undefined)
          }}
          onChecks={() => run(checkBranch)}
          onTrial={(message) =>
            run(async () =>
              setTrial(
                await trialMerge({
                  data: {
                    env,
                    branch: selected.name,
                    target: target.name,
                    sourceHash: selected.hash,
                    targetHash: target.hash,
                    message,
                    confirmed: true,
                    idempotencyKey: operationKey(
                      env,
                      'trial',
                      `${selected.name}@${selected.hash}->${target.name}@${target.hash}:${message}`,
                    ),
                  },
                }),
              ),
            )
          }
          onMerge={(message, signatureTargetVersion) =>
            run(
              () =>
                signAndMerge({
                  data: {
                    env,
                    branch: selected.name,
                    target: target.name,
                    sourceHash: selected.hash,
                    targetHash: target.hash,
                    message,
                    signatureTargetVersion,
                    confirmed: true,
                    idempotencyKey: operationKey(
                      env,
                      'flow',
                      signatureTargetVersion,
                    ),
                    signatureKey: operationKey(
                      env,
                      'signature',
                      `${signatureTargetVersion}:${message}`,
                    ),
                    mergeKey: operationKey(
                      env,
                      'merge',
                      `${signatureTargetVersion}:${message}`,
                    ),
                  },
                }),
              refresh,
            )
          }
        />
      ) : null}
    </>
  )
}
