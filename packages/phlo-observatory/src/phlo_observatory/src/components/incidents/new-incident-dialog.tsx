/** Collects and submits the fields required to create an incident. */
import * as React from 'react'
import { FileWarningIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ChoiceItem, RadioGroup } from '@/components/ui/checkbox'
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
import { Select } from '@/components/ui/select'
import { Mono } from '@/components/phlo/status'

const kinds = ['manual', 'freshness', 'schema', 'audit', 'data_quality']

export interface NewIncidentValues {
  title: string
  kind: string
  assetId: string
  description: string
}

export function NewIncidentDialog({
  open,
  busy,
  error,
  assets,
  onClose,
  onCreate,
}: {
  open: boolean
  busy: boolean
  error?: string
  assets: Array<string>
  onClose: () => void
  onCreate: (values: NewIncidentValues) => void
}) {
  const [title, setTitle] = React.useState('')
  const [kind, setKind] = React.useState('manual')
  const [assetId, setAssetId] = React.useState('')
  const [description, setDescription] = React.useState('')
  return (
    <Dialog open={open} onOpenChange={(value) => !value && onClose()}>
      <DialogContent className="max-w-[640px]">
        <form
          onSubmit={(event) => {
            event.preventDefault()
            onCreate({
              title: title.trim(),
              kind: kind.trim(),
              assetId: assetId.trim(),
              description: description.trim(),
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
              <span className="text-[12.5px] text-muted-foreground">
                Choose the persisted evidence category.
              </span>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="incident-asset">Affected asset</Label>
              {assets.length ? (
                <Select
                  id="incident-asset"
                  value={assetId}
                  onValueChange={setAssetId}
                  options={[
                    { value: '', label: 'Select an asset…' },
                    ...assets.map((asset) => ({
                      value: asset,
                      label: <Mono>{asset}</Mono>,
                    })),
                  ]}
                />
              ) : (
                <Input
                  id="incident-asset"
                  required
                  maxLength={512}
                  value={assetId}
                  onChange={(e) => setAssetId(e.target.value)}
                  placeholder="Asset ID"
                />
              )}
              <span className="text-[12.5px] text-muted-foreground">
                Only assets returned by the incident service are offered.
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
                busy || !title.trim() || !assetId.trim() || !description.trim()
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
