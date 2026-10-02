/** Collects and submits the fields required to create an incident. */
import * as React from 'react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Field, FieldLabel } from '@/components/ui/field'
import { Input, Textarea } from '@/components/ui/input'

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
  onClose,
  onCreate,
}: {
  open: boolean
  busy: boolean
  error?: string
  onClose: () => void
  onCreate: (values: NewIncidentValues) => void
}) {
  const [title, setTitle] = React.useState('')
  const [kind, setKind] = React.useState('manual')
  const [assetId, setAssetId] = React.useState('')
  const [description, setDescription] = React.useState('')
  return (
    <Dialog open={open} onOpenChange={(value) => !value && onClose()}>
      <DialogContent className="max-w-[600px]">
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
          <DialogHeader>
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
            <Field>
              <FieldLabel>Asset ID</FieldLabel>
              <Input
                required
                maxLength={512}
                value={assetId}
                onChange={(e) => setAssetId(e.target.value)}
                placeholder="bronze.orders"
              />
            </Field>
            <Field>
              <FieldLabel>Kind</FieldLabel>
              <Input
                required
                maxLength={100}
                value={kind}
                onChange={(e) => setKind(e.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel>Evidence</FieldLabel>
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
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="submit"
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
