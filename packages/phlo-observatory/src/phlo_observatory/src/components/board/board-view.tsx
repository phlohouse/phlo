/**
 * The board — the deck's center surface.
 *
 * Decision-first mission control for the lake. DECISIONS is the publish
 * queue: every row is a dataset with a policy verdict, its blockers or
 * missing evidence spelled out, and the publish/retire transitions the
 * verdict permits — executable in place. IN FLIGHT is work moving through
 * the lake right now. THE LAKE beneath is the inventory that gives those
 * rows their context — kept deliberately quiet.
 */

import { ArrowDownToLine, CheckCheck, ChevronRight } from 'lucide-react'

import type { BoardDecision, BoardModel } from './board-model'
import type { LakeModel } from '@/components/lake/lake-model'
import { InboundCard, LakeView } from '@/components/lake/lake-view'
import { ObservActionButton } from '@/components/observatory/actions'
import { HealthDot } from '@/components/observatory/status'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const KIND_LABEL: Record<BoardDecision['kind'], string> = {
  blocked: 'Blocked',
  ready: 'Ready to publish',
  warning: 'Publishable with warnings',
  evidence: 'Needs evidence',
}

const KIND_CHIP: Record<BoardDecision['kind'], string> = {
  blocked: 'bg-band-danger text-print-red',
  ready: 'bg-band-ok text-ok-ink',
  warning: 'bg-band-warn text-amber-ink',
  evidence: 'bg-band text-ink-soft',
}

function reasonLine(decision: BoardDecision): string {
  const first =
    decision.blockers[0] ?? decision.warnings[0] ?? decision.missing[0]
  if (first) {
    const rest =
      decision.blockers.length +
      decision.warnings.length +
      decision.missing.length -
      1
    return rest > 0 ? `${first} · +${rest} more` : first
  }
  return `Policy ${decision.policy} passed`
}

const KIND_BAR: Record<BoardDecision['kind'], string> = {
  blocked: 'border-l-print-red',
  ready: 'border-l-ok-ink',
  warning: 'border-l-amber-ink',
  evidence: 'border-l-transparent',
}

function DecisionRow({
  decision,
  onFocus,
  onRun,
}: {
  decision: BoardDecision
  onFocus: (target: string) => void
  onRun: (action: BoardDecision['actions'][number]) => void
}) {
  return (
    <div
      className={cn(
        'band-hover flex items-center gap-3 border-l-2 px-3 py-2',
        KIND_BAR[decision.kind],
      )}
    >
      <HealthDot className="flex-none" state={decision.state} />

      <button
        className="min-w-0 flex-1 text-left"
        onClick={() => onFocus(decision.focusTarget)}
        type="button"
      >
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="text-ink truncate text-xs font-medium">
            {decision.name}
          </span>
          <span className="text-ink-faint flex-none text-[10px] uppercase tracking-wide">
            {decision.publication}
          </span>
          {decision.owner && (
            <span className="text-ink-faint hidden flex-none font-mono text-[10px] md:inline">
              {decision.owner}
            </span>
          )}
          <span
            className={cn(
              'flex-none rounded px-1.5 py-px text-[9px] font-semibold uppercase tracking-wider',
              KIND_CHIP[decision.kind],
            )}
          >
            {KIND_LABEL[decision.kind]}
          </span>
        </div>
        <div className="text-ink-soft mt-0.5 truncate text-[10px]">
          {reasonLine(decision)}
        </div>
      </button>

      <div className="flex flex-none items-center gap-1.5">
        {decision.actions.map((action) => (
          <ObservActionButton action={action} key={action.id} onRun={onRun} />
        ))}
        <Button
          aria-label={`Inspect ${decision.name}`}
          onClick={() => onFocus(decision.focusTarget)}
          size="icon-xs"
          variant="ghost"
        >
          <ChevronRight className="size-3.5" />
        </Button>
      </div>
    </div>
  )
}

function CountStat({
  count,
  label,
  tone,
}: {
  count: number
  label: string
  tone?: string
}) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span
        className={cn('font-mono text-xs font-semibold', tone ?? 'text-ink')}
      >
        {count}
      </span>
      <span className="text-ink-faint text-[10px] uppercase tracking-wider">
        {label}
      </span>
    </span>
  )
}

export function BoardView({
  board,
  lake,
  onFocus,
  onRun,
}: {
  board: BoardModel
  lake: LakeModel
  onFocus: (target: string) => void
  onRun: (action: BoardDecision['actions'][number]) => void
}) {
  const { counts } = board
  const quiet = board.decisions.length === 0 && lake.inbound.length === 0

  return (
    <div className="bg-canvas scrollbar-thin h-full overflow-y-auto">
      {/* Subbar — the lake's decision posture at a glance. */}
      <div className="border-rule bg-raised sticky top-0 z-10 flex h-8 flex-none items-center gap-5 border-b px-3">
        <CountStat
          count={counts.ready}
          label="ready"
          tone={counts.ready > 0 ? 'text-ok-ink' : undefined}
        />
        <CountStat
          count={counts.blocked}
          label="blocked"
          tone={counts.blocked > 0 ? 'text-print-red' : undefined}
        />
        <CountStat
          count={counts.warning}
          label="warnings"
          tone={counts.warning > 0 ? 'text-amber-ink' : undefined}
        />
        <CountStat
          count={counts.evidence}
          label="needs evidence"
          tone={counts.evidence > 0 ? 'text-ink-soft' : undefined}
        />
        <CountStat
          count={lake.inbound.length}
          label="in flight"
          tone={lake.inbound.length > 0 ? 'text-blue' : undefined}
        />
        <CountStat
          count={counts.stale}
          label="stale"
          tone={counts.stale > 0 ? 'text-amber-ink' : undefined}
        />
        <span className="text-ink-faint ml-auto hidden font-mono text-[10px] sm:inline">
          {lake.total} datasets
        </span>
      </div>

      <div className="flex flex-col gap-3 p-3">
        {/* Decisions — the publish queue. */}
        <section className="border-rule bg-panel overflow-hidden rounded-lg border shadow-sm">
          <header className="panel-heading">
            <CheckCheck className="size-3" />
            Decisions
            <span className="text-ink-faint font-mono normal-case tracking-normal">
              {board.total}
            </span>
          </header>
          {board.decisions.length === 0 ? (
            <div className="text-ink-faint px-3 py-4 text-center text-[11px]">
              Nothing to decide — every dataset is either published and healthy
              or not yet promotable.
            </div>
          ) : (
            <div className="divide-rule divide-y">
              {board.decisions.map((decision) => (
                <DecisionRow
                  decision={decision}
                  key={decision.datasetId}
                  onFocus={onFocus}
                  onRun={onRun}
                />
              ))}
              {board.total > board.decisions.length && (
                <div className="text-ink-faint px-3 py-1.5 text-center text-[10px]">
                  +{board.total - board.decisions.length} more need decisions
                </div>
              )}
            </div>
          )}
        </section>

        {/* In flight — work moving through the lake right now. */}
        {lake.inbound.length > 0 && (
          <section className="border-rule bg-panel overflow-hidden rounded-lg border shadow-sm">
            <header className="panel-heading">
              <ArrowDownToLine className="size-3" />
              In flight
              <span className="text-ink-faint font-mono normal-case tracking-normal">
                {lake.inbound.length}
              </span>
            </header>
            <div className="scrollbar-thin flex gap-2 overflow-x-auto p-2">
              {lake.inbound.map((inbound) => (
                <InboundCard
                  inbound={inbound}
                  key={inbound.id}
                  onFocus={onFocus}
                />
              ))}
            </div>
          </section>
        )}

        {/* The lake — the inventory, kept quiet beneath the work. */}
        <section className="border-rule bg-panel overflow-hidden rounded-lg border shadow-sm">
          <header className="panel-heading">
            The lake
            <span className="text-ink-faint font-mono normal-case tracking-normal">
              {lake.total}
            </span>
          </header>
          <div className="h-[22rem]">
            <LakeView model={lake} onFocus={onFocus} showInbound={false} />
          </div>
        </section>

        {quiet && lake.total === 0 && (
          <div className="text-ink-faint py-8 text-center text-xs">
            The lakehouse is quiet — no datasets, no work in flight.
          </div>
        )}
      </div>
    </div>
  )
}
