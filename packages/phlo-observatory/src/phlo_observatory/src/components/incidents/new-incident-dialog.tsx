/** Collects and submits the fields required to create an incident. */
import * as React from 'react'
import { FileWarningIcon, XIcon } from 'lucide-react'
import type { z } from 'zod'
import { Button } from '@/components/ui/button'
import { CheckLine, ChoiceItem, RadioGroup } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Field, FieldLabel, Label } from '@/components/ui/field'
import { Input, Textarea } from '@/components/ui/input'
import { Mono } from '@/components/phlo/status'
import { severitySchema } from '@/lib/data/api/incidents'

const kinds = [
  'freshness',
  'schema',
  'audit',
  'data_quality',
  'catalog',
  'performance',
]

export interface NewIncidentValues {
  title: string
  kind: string
  assets: Array<string>
  severity: z.infer<typeof severitySchema>
  owner: string
  description: string
  notifyQa: boolean
  pauseDownstream: boolean
}

export function NewIncidentDialog({
  open,
  busy,
  error,
  assets,
  owners = [],
  onClose,
  onCreate,
}: {
  open: boolean
  busy: boolean
  error?: string
  assets: Array<string>
  owners?: Array<string>
  onClose: () => void
  onCreate: (values: NewIncidentValues) => void
}) {
  const [title, setTitle] = React.useState('')
  const [kind, setKind] = React.useState('data_quality')
  const [tokens, setTokens] = React.useState<Array<string>>([])
  const [assetDraft, setAssetDraft] = React.useState('')
  const [severity, setSeverity] =
    React.useState<NewIncidentValues['severity']>('medium')
  const [owner, setOwner] = React.useState('')
  const [description, setDescription] = React.useState('')
  const [notifyQa, setNotifyQa] = React.useState(false)
  const [pauseDownstream, setPauseDownstream] = React.useState(false)
  const addAsset = () => {
    const value = assetDraft.trim()
    if (value && !tokens.includes(value))
      setTokens((current) => [...current, value])
    setAssetDraft('')
  }
  return (
    <Dialog open={open} onOpenChange={(value) => !value && onClose()}>
      <DialogContent className="max-w-[640px]">
        <form
          className="flex min-h-0 flex-col"
          onSubmit={(event) => {
            event.preventDefault()
            onCreate({
              title: title.trim(),
              kind: kind.trim(),
              assets: tokens,
              severity,
              owner: owner.trim(),
              description: description.trim(),
              notifyQa,
              pauseDownstream,
            })
          }}
        >
          <DialogHeader className="flex-row items-center gap-2.5">
            <FileWarningIcon className="size-[18px] text-bad" aria-hidden />
            <DialogTitle>New incident</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <Field>
              <FieldLabel>Title</FieldLabel>
              <Input
                required
                maxLength={500}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </Field>
            <div className="flex flex-col gap-1.5">
              <span id="incident-kind" className="text-[13.5px] font-medium">
                Kind
              </span>
              <RadioGroup
                aria-labelledby="incident-kind"
                value={kind}
                onValueChange={(value) => setKind(String(value))}
              >
                {kinds.map((value) => (
                  <ChoiceItem key={value} value={value}>
                    {value.replaceAll('_', ' ')}
                  </ChoiceItem>
                ))}
              </RadioGroup>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <span
                  id="incident-severity"
                  className="text-[13.5px] font-medium"
                >
                  Severity
                </span>
                <RadioGroup
                  aria-labelledby="incident-severity"
                  value={severity}
                  onValueChange={(value) =>
                    setSeverity(severitySchema.parse(value))
                  }
                >
                  {severitySchema.options.map((value) => (
                    <ChoiceItem key={value} value={value}>
                      {value[0].toUpperCase() + value.slice(1)}
                    </ChoiceItem>
                  ))}
                </RadioGroup>
                <span className="text-[12.5px] text-muted-foreground">
                  Recorded for triage and notification routing.
                </span>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="incident-owner">Owner</Label>
                <Input
                  id="incident-owner"
                  list="incident-owners"
                  maxLength={512}
                  value={owner}
                  onChange={(event) => setOwner(event.target.value)}
                  placeholder="Unassigned"
                />
                <datalist id="incident-owners">
                  {owners.map((value) => (
                    <option key={value} value={value} />
                  ))}
                </datalist>
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="incident-asset">Affected assets</Label>
              <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-input bg-card p-2">
                {tokens.map((asset) => (
                  <span
                    key={asset}
                    className="inline-flex items-center gap-1 rounded bg-soft px-2 py-1"
                  >
                    <Mono>{asset}</Mono>
                    <button
                      type="button"
                      aria-label={`Remove ${asset}`}
                      onClick={() =>
                        setTokens((current) =>
                          current.filter((value) => value !== asset),
                        )
                      }
                    >
                      <XIcon className="size-3.5" />
                    </button>
                  </span>
                ))}
                <input
                  id="incident-asset"
                  list="incident-assets"
                  maxLength={512}
                  value={assetDraft}
                  onChange={(event) => setAssetDraft(event.target.value)}
                  onBlur={addAsset}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ',') {
                      event.preventDefault()
                      addAsset()
                    }
                  }}
                  placeholder="Add a table…"
                  className="h-8 min-w-[140px] flex-1 bg-transparent px-1 text-sm outline-none"
                />
                <datalist id="incident-assets">
                  {assets.map((asset) => (
                    <option key={asset} value={asset} />
                  ))}
                </datalist>
              </div>
              <span className="text-[12.5px] text-muted-foreground">
                {tokens.length
                  ? `${tokens.length} affected assets`
                  : 'Add the tables this affects. Press Enter to add.'}
              </span>
            </div>
            <Field>
              <FieldLabel>What’s wrong</FieldLabel>
              <Textarea
                required
                rows={4}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Describe the observed evidence"
              />
            </Field>
            <div className="flex flex-col gap-2.5">
              <CheckLine
                checked={notifyQa}
                onCheckedChange={(checked) => setNotifyQa(checked === true)}
              >
                Notify QA, using the configured QA alert destination
              </CheckLine>
              <CheckLine
                checked={pauseDownstream}
                onCheckedChange={(checked) =>
                  setPauseDownstream(checked === true)
                }
              >
                Pause downstream gold model schedules
              </CheckLine>
              <span className="text-xs text-muted-foreground">
                Requires configured providers and permission. Delivery failures
                remain visible on the incident.
              </span>
            </div>
            {error ? (
              <p role="alert" className="m-0 text-sm text-bad-text">
                {error}
              </p>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <span className="hidden text-[13px] text-muted-foreground sm:inline">
              Creates persisted incident evidence
            </span>
            <Button
              type="button"
              variant="outline"
              size="lg"
              className="ml-auto bg-card"
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              size="lg"
              disabled={
                busy || !title.trim() || !tokens.length || !description.trim()
              }
            >
              {busy ? 'Creating…' : 'Create incident'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
