/** Keeps observed pre-merge checks visible outside the approval dialog. */
import { CircleCheckIcon, CircleDashedIcon, CircleXIcon } from 'lucide-react'
import type { BranchCheck } from '@/lib/data/api/branches'
import { ActionButton as Button } from '@/components/phlo/action-button'

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
      <details className="text-xs leading-relaxed text-muted-foreground">
        <summary className="cursor-pointer">Release prerequisites</summary>
        <ol className="mt-2 list-decimal space-y-2 pl-4">
          <li>
            Configure and run tests, schema contracts and downstream audits.
            Unavailable or missing evidence is not a pass; inspect each recorded
            result above.
          </li>
          <li>
            Run a trial merge against the current source and target hashes.
            Reference changes invalidate earlier evidence.
          </li>
          <li>
            Review the changes and provide the required fresh approval signature
            and MFA in the merge dialog. Enabled controls are coarse permission
            checks, not proof that signing or release will succeed.
          </li>
        </ol>
      </details>
    </section>
  )
}
