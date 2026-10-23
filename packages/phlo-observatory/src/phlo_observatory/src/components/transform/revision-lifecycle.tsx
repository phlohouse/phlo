/** Saved SQL stays reviewable when archived; source handoff never applies files. */
import type { TransformDraft } from '@/lib/data/api/transform-editor'
import {
  archiveTransformDraft,
  getTransformHandoff,
} from '@/lib/data/api/transform-editor'
import {
  ArchiveEvidenceAction,
  SourceHandoffPanel,
} from '@/components/phlo/retained-evidence'
import { ActionGroup } from '@/components/ui/action-group'

export function RevisionLifecycle({
  env,
  model,
  draft,
  disabled,
  onChanged,
}: {
  env: string
  model: string
  draft: TransformDraft
  disabled: boolean
  onChanged: (value: TransformDraft) => void
}) {
  const identity = {
    env,
    model,
    draft_id: draft.draft_id,
    revision: draft.revision,
  }
  return (
    <section
      aria-label="Saved revision lifecycle"
      className="grid shrink-0 gap-3 border-t border-line p-4"
    >
      <ActionGroup>
        <ArchiveEvidenceAction
          archived={Boolean(draft.archived_at)}
          reason={draft.archive_reason}
          operation="v1_transform_model_draft_archive"
          disabled={disabled}
          onChange={async (input) =>
            onChanged(
              await archiveTransformDraft({ data: { ...identity, ...input } }),
            )
          }
        />
      </ActionGroup>
      {draft.archived_at ? (
        <p role="status" className="m-0 text-xs text-muted-foreground">
          Archived revision · {draft.archive_reason}. Source and native evidence
          remain readable. Restore before editing, compiling or executing.
        </p>
      ) : null}
      <SourceHandoffPanel
        key={draft.archived_at ?? ''}
        operation="v1_transform_model_draft_handoff"
        disabled={disabled}
        load={() => getTransformHandoff({ data: identity })}
      />
    </section>
  )
}
