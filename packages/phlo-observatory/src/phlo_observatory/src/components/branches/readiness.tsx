/** Keeps observed pre-merge checks visible outside the approval dialog. */
import { CircleCheckIcon, CircleDashedIcon, CircleXIcon } from 'lucide-react'
import type { BranchCheck } from '@/lib/data/api/branches'
import { Button } from '@/components/ui/button'

export function BranchReadiness({
  checks,
  busy,
  onChecks,
  branchName,
}: {
  checks?: Array<BranchCheck>
  busy: boolean
  onChecks: () => void
  branchName?: string
}) {
  return (
    <section aria-label="Pre-merge checks" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="m-0 text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Pre-merge checks
        </h3>
        <Button
          operation="v1_branch_checks"
          actionContext={branchName ? { branch_name: branchName } : undefined}
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={onChecks}
        >
          Run checks
        </Button>
      </div>
      <ul className="m-0 list-none divide-y divide-line-soft p-0">
        {(['contracts', 'tests', 'audits'] as const).map((name) => {
          const check = checks?.find((item) => item.name === name)
          const status = check?.status ?? 'not run'
          const Icon =
            status === 'passed'
              ? CircleCheckIcon
              : status === 'failed'
                ? CircleXIcon
                : CircleDashedIcon
          return (
            <li key={name} className="flex items-start gap-3 py-3">
              <Icon
                className={`mt-0.5 size-4 shrink-0 ${status === 'passed' ? 'text-ok-text' : status === 'failed' ? 'text-bad-text' : 'text-muted-foreground'}`}
              />
              <div className="min-w-0">
                <p className="m-0 text-sm">
                  {name === 'contracts'
                    ? 'Schema contracts'
                    : name === 'audits'
                      ? 'Downstream audits'
                      : 'Tests'}{' '}
                  <span className="text-xs text-muted-foreground">
                    · {status}
                  </span>
                </p>
                {check?.message ? (
                  <p className="mt-1 mb-0 text-xs leading-relaxed text-muted-foreground">
                    {check.message}
                  </p>
                ) : null}
                {check?.run_id ? (
                  <p
                    className="mt-1 mb-0 truncate font-mono text-xs text-muted-foreground"
                    title={check.run_id}
                  >
                    {check.run_id}
                  </p>
                ) : null}
              </div>
            </li>
          )
        })}
      </ul>
      <p className="m-0 text-xs leading-relaxed text-muted-foreground">
        Checks apply to this pinned branch revision. Trial merge and a fresh
        approval signature remain required.
      </p>
    </section>
  )
}
