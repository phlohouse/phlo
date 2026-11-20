/** Run the saved notebook on a cron schedule, as the person who sets it. */
import * as React from 'react'
import type { NotebookSchedule } from '@/lib/data/api/notebooks'
import { CRON_PRESETS, describeCron } from '@/lib/notebook'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'

export type ScheduleChange = {
  cron: string | null
  runEnv: string
  enabled: boolean
}

export function ScheduleDialog({
  open,
  onOpenChange,
  env,
  environments,
  schedule,
  schedulerOn,
  onSave,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  env: string
  environments: ReadonlyArray<string>
  schedule: NotebookSchedule | null
  /** Whether this API runs the scheduler at all */
  schedulerOn: boolean
  onSave: (change: ScheduleChange) => Promise<string | null>
}) {
  const [cron, setCron] = React.useState(schedule?.cron ?? CRON_PRESETS[1].cron)
  const [runEnv, setRunEnv] = React.useState(schedule?.env ?? env)
  const [enabled, setEnabled] = React.useState(schedule?.enabled ?? true)
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  React.useEffect(() => {
    if (!open) return
    setCron(schedule?.cron ?? CRON_PRESETS[1].cron)
    setRunEnv(schedule?.env ?? env)
    setEnabled(schedule?.enabled ?? true)
    setError(null)
  }, [open, schedule, env])
  const save = async (change: ScheduleChange) => {
    setBusy(true)
    const failure = await onSave(change)
    setBusy(false)
    if (failure) setError(failure)
    else onOpenChange(false)
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[520px]">
        <DialogHeader>
          <DialogTitle>Schedule</DialogTitle>
          <DialogDescription>
            Runs the saved notebook top to bottom in its own kernel. Results
            appear in Notebook runs and as each cell&apos;s last output.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-3.5">
          {schedulerOn ? null : (
            <p className="rounded-lg border border-warn-soft bg-warn-wash px-3 py-2 text-[12.5px] text-warn-ink">
              The scheduler is off on this Phlo API, so nothing will run until
              an operator sets PHLO_NOTEBOOK_SCHEDULES=1. You can still save a
              schedule.
            </p>
          )}
          <div className="flex flex-wrap gap-1.5">
            {CRON_PRESETS.map((preset) => (
              <button
                key={preset.cron}
                type="button"
                onClick={() => setCron(preset.cron)}
                className={cn(
                  'h-7 rounded-md border border-border px-2.5 text-[12.5px] text-text-2 hover:bg-soft',
                  cron === preset.cron &&
                    'border-primary bg-primary-soft text-foreground',
                )}
              >
                {preset.label}
              </button>
            ))}
          </div>
          <label className="flex flex-col gap-1 text-[13px]">
            Cron, in UTC
            <Input
              aria-label="Cron expression"
              value={cron}
              onChange={(event) => {
                setCron(event.target.value)
                setError(null)
              }}
              className="font-mono text-[12.5px]"
            />
            <span className="text-xs text-muted-foreground">
              {describeCron(cron)} (UTC). Minute, hour, day of month, month, day
              of week.
            </span>
          </label>
          <label className="flex flex-col gap-1 text-[13px]">
            Environment
            <Select
              aria-label="Run in environment"
              value={runEnv}
              onValueChange={setRunEnv}
              options={environments.map((item) => ({
                value: item,
                label: item,
              }))}
            />
          </label>
          <label className="flex items-center gap-2.5 text-[13px]">
            <Switch
              checked={enabled}
              onCheckedChange={setEnabled}
              aria-label="Schedule on"
            />
            {enabled ? 'On' : 'Paused'}
          </label>
          <p className="text-xs text-muted-foreground">
            {schedule
              ? schedule.approved
                ? `Runs as ${schedule.owner}. Saving makes you the owner, so runs use your access.`
                : `Paused: the notebook changed since ${schedule.owner} scheduled it. Save to resume it with your access.`
              : 'Runs use your access. A missed run is not caught up later.'}
          </p>
          {error ? (
            <p role="alert" className="text-xs text-bad-text">
              {error}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter className="flex-wrap">
          {schedule ? (
            <Button
              variant="ghost"
              className="mr-auto"
              disabled={busy}
              onClick={() => void save({ cron: null, runEnv, enabled })}
            >
              Remove schedule
            </Button>
          ) : null}
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={busy || !cron.trim()}
            onClick={() => void save({ cron: cron.trim(), runEnv, enabled })}
          >
            {busy ? 'Saving…' : 'Save schedule'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
