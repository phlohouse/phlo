/** Saved SQL stays reviewable when archived; source handoff never applies files. */
import type { TransformDraft } from '@/lib/data/api/transform-editor'
import {
  archiveTransformDraft,
  getTransformHandoff,
  reclaimTransformDraft,
} from '@/lib/data/api/transform-editor'
import { ReclaimAction } from '@/components/phlo/reclaim-action'
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
          disabled={
            disabled || Boolean(draft.reclaimed_at || draft.reclaim_started_at)
          }
          onChange={async (input) =>
            onChanged(
              await archiveTransformDraft({ data: { ...identity, ...input } }),
            )
          }
        />
        {draft.archived_at && !draft.reclaimed_at ? (
          <ReclaimAction
            identity={`${draft.draft_id}:${draft.revision}`}
            label={
              draft.reclaim_started_at
                ? 'Retry payload reclamation'
                : 'Reclaim revision payload'
            }
            description="Permanently remove the copied dependency workspace for this archived revision. Checksum-verified SQL source handoff, identity, hashes and native operation evidence remain. Provider tables and catalogs are unchanged; mutation fences are not released."
            operation="v1_transform_model_draft_reclaim"
            disabled={disabled}
            onReclaim={async (reason) =>
              onChanged(
                await reclaimTransformDraft({
                  data: {
                    ...identity,
                    confirmed: true,
                    reason,
                    expected_revision_digest: draft.revision_digest,
                  },
                }),
              )
            }
          />
        ) : null}
      </ActionGroup>
      {draft.archived_at ? (
        <p role="status" className="m-0 text-xs text-muted-foreground">
          Archived revision · {draft.archive_reason}. Source and native evidence
          remain readable.{' '}
          {draft.reclaimed_at || draft.reclaim_started_at
            ? `Local workspace reclamation ${draft.reclaimed_at ? 'completed' : 'started'}. Restoration, compilation and execution are unavailable. ${draft.reclaimed_bytes ?? 0} local bytes reclaimed.`
            : 'Restore before editing, compiling or executing.'}
        </p>
      ) : null}
      <SourceHandoffPanel
        key={JSON.stringify([
          draft.archived_at,
          draft.reclaimed_at,
          draft.reclaim_started_at,
        ])}
        operation="v1_transform_model_draft_handoff"
        disabled={disabled}
        load={() => getTransformHandoff({ data: identity })}
      />
    </section>
  )
}
