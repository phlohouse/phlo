/**
 * Workflow builder route. The loader snapshots wizard inputs before render;
 * the canvas builder composes source, transform, quality, and publish
 * stages from extension-contributed wizard steps and applies the resulting
 * proposal.
 */
import { createFileRoute } from '@tanstack/react-router'
import { Button, IconButton } from '@primer/react'
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CheckCircleIcon,
  FileCodeIcon,
  PlusIcon,
  TrashIcon,
} from '@primer/octicons-react'
import { WandSparkles } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ChangeEvent, ReactNode } from 'react'

import type {
  ObservatoryAsset,
  ObservatoryQualityCheck,
  ObservatoryResourceResult,
  ObservatoryTable,
  ObservatoryWorkflowApplyAction,
  ObservatoryWorkflowGraph,
  ObservatoryWorkflowProposal,
  ObservatoryWorkflowWizardContribution,
  ObservatoryWorkflowWizardField,
  ObservatoryWorkflowWizardPayload,
} from '@/observatory/api/types'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import {
  createObservatoryWorkflowProposal,
  getObservatoryAssetRecords,
  getObservatoryQualityRecords,
  getObservatoryTableRecords,
  getObservatoryWorkflowWizard,
  runObservatoryWorkflowAction,
} from '@/observatory/api/resources'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import {
  invalidateCachedResource,
  loadCachedResource,
  useLiveResource,
} from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/workflows/new')({
  loader: loadWorkflowBuilderSnapshot,
  component: ObservatoryWorkflowCanvasBuilderRoute,
})

type WorkflowNodeData = {
  contributionId: string
  description: string
  label: string
  packageName: string
  stage: 'source' | 'transform' | 'quality' | 'publish'
}

type WorkflowNode = {
  id: string
  data: WorkflowNodeData
}
type FormValues = Record<string, Record<string, string>>
type WizardStep = 'info' | 'graph' | 'proposal'
type LakehouseTemplate = {
  id: string
  label: string
  summary: string
  workflowName: string
  domain: string
  focusTable: ObservatoryTable | null
  focusAsset: ObservatoryAsset | null
  quality: Array<ObservatoryQualityCheck>
  contributionIds: Array<string>
}
export type WorkflowBuilderSnapshot = {
  assets: ObservatoryResourceResult<Array<ObservatoryAsset>>
  quality: ObservatoryResourceResult<Array<ObservatoryQualityCheck>>
  tables: ObservatoryResourceResult<Array<ObservatoryTable>>
  wizard: ObservatoryResourceResult<ObservatoryWorkflowWizardPayload>
}

const STAGE_LABELS: Record<string, string> = {
  source: 'Source',
  transform: 'Transform',
  quality: 'Quality',
  publish: 'Publish',
}

const WORKFLOW_STEPS: Array<{
  id: WizardStep
  label: string
  description: string
}> = [
  {
    id: 'info',
    label: 'Workflow info',
    description: 'Name and domain',
  },
  {
    id: 'graph',
    label: 'Build graph',
    description: 'Pipeline steps',
  },
  {
    id: 'proposal',
    label: 'Review proposal',
    description: 'Files and apply action',
  },
]

export async function loadWorkflowBuilderSnapshot(): Promise<WorkflowBuilderSnapshot> {
  const [tables, assets, quality, wizard] = await Promise.all([
    getObservatoryTableRecords(),
    getObservatoryAssetRecords(),
    getObservatoryQualityRecords(),
    getObservatoryWorkflowWizard(),
  ])
  return { assets, quality, tables, wizard }
}

function ObservatoryWorkflowCanvasBuilderRoute() {
  const snapshot = Route.useLoaderData()
  return <WorkflowCanvasBuilder initialSnapshot={snapshot} />
}

export function WorkflowCanvasBuilder({
  initialSnapshot,
}: {
  initialSnapshot?: WorkflowBuilderSnapshot
}) {
  return useWorkflowCanvasBuilder(initialSnapshot)
}

function useWorkflowCanvasBuilder(initialSnapshot?: WorkflowBuilderSnapshot) {
  const tableResult = useLiveResource(
    getObservatoryTableRecords,
    120_000,
    'observatory:tables',
  )
  const assetResult = useLiveResource(
    getObservatoryAssetRecords,
    120_000,
    'observatory:assets',
  )
  const qualityResult = useLiveResource(
    getObservatoryQualityRecords,
    120_000,
    'observatory:quality',
  )
  const effectiveTableResult =
    tableResult.data === null && initialSnapshot?.tables.data
      ? initialSnapshot.tables
      : tableResult
  const effectiveAssetResult =
    assetResult.data === null && initialSnapshot?.assets.data
      ? initialSnapshot.assets
      : assetResult
  const effectiveQualityResult =
    qualityResult.data === null && initialSnapshot?.quality.data
      ? initialSnapshot.quality
      : qualityResult
  const [wizard, setWizard] = useState<
    ObservatoryResourceResult<ObservatoryWorkflowWizardPayload>
  >(initialSnapshot?.wizard ?? { data: null, error: null })
  const [nodes, setNodes] = useState<Array<WorkflowNode>>([])
  const [values, setValues] = useState<FormValues>({})
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [insertIndex, setInsertIndex] = useState<number | null>(null)
  const [addMenuOpen, setAddMenuOpen] = useState(false)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [workflowName, setWorkflowName] = useState('lakehouse_workflow')
  const [domain, setDomain] = useState('lakehouse')
  const [activeStep, setActiveStep] = useState<WizardStep>('info')
  const [proposal, setProposal] = useState<
    ObservatoryResourceResult<ObservatoryWorkflowProposal>
  >({
    data: null,
    error: null,
  })
  const [proposalLoading, setProposalLoading] = useState(false)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const activeStepIndex = WORKFLOW_STEPS.findIndex(
    (step) => step.id === activeStep,
  )
  const applyWizardPayload = useCallback(
    (next: ObservatoryResourceResult<ObservatoryWorkflowWizardPayload>) => {
      setWizard(next)
      const contributions = next.data?.contributions ?? []
      const starterNodes = starterGraph(contributions).nodes
      setNodes(starterNodes)
      setValues(starterValues(contributions, starterNodes))
      setSelectedNodeId(starterNodes[0]?.id ?? null)
      setInsertIndex(starterNodes.length)
    },
    [],
  )

  useEffect(() => {
    let cancelled = false
    void loadCachedResource(
      'observatory:workflow-wizard',
      getObservatoryWorkflowWizard,
      {
        force: true,
        staleMs: 60_000,
      },
    ).then((next) => {
      if (cancelled) return
      if (!next.data && initialSnapshot?.wizard.data) return
      applyWizardPayload(next)
    })
    return () => {
      cancelled = true
    }
  }, [applyWizardPayload, initialSnapshot?.wizard.data])

  const contributions = wizard.data?.contributions ?? []
  const contributionById = useMemo(
    () => new Map(contributions.map((item) => [item.id, item])),
    [contributions],
  )
  const lakehouseTemplates = useMemo(
    () =>
      buildLakehouseTemplates(
        effectiveTableResult.data ?? [],
        effectiveAssetResult.data ?? [],
        effectiveQualityResult.data ?? [],
      ),
    [
      effectiveAssetResult.data,
      effectiveQualityResult.data,
      effectiveTableResult.data,
    ],
  )
  const selectedNode = nodes.find((node) => node.id === selectedNodeId) ?? null
  const selectedContribution = selectedNode
    ? contributionById.get(selectedNode.data.contributionId)
    : null

  function addContribution(
    contribution: ObservatoryWorkflowWizardContribution,
  ) {
    const nodeId = `${contribution.id}-${crypto.randomUUID()}`
    const node = toCanvasNode(contribution, nodeId)
    setNodes((current) => {
      const next = [...current]
      const targetIndex =
        insertIndex === null
          ? current.length
          : Math.max(0, Math.min(insertIndex, current.length))
      next.splice(targetIndex, 0, node)
      setInsertIndex(targetIndex + 1)
      return next
    })
    setValues((current) => ({
      ...current,
      [nodeId]: defaultsForContribution(contribution),
    }))
    setSelectedNodeId(nodeId)
    setInspectorOpen(true)
    setAddMenuOpen(false)
  }

  function applyLakehouseTemplate(template: LakehouseTemplate) {
    const contributionsById = new Map(
      contributions.map((contribution) => [contribution.id, contribution]),
    )
    const templateNodes = template.contributionIds.flatMap((id, index) => {
      const contribution = contributionsById.get(id)
      return contribution
        ? [
            toCanvasNode(
              contribution,
              `template-${index + 1}-${contribution.id}`,
            ),
          ]
        : []
    })
    const templateValues = templateNodes.reduce<FormValues>((current, node) => {
      const contribution = contributionsById.get(node.data.contributionId)
      current[node.id] = contribution
        ? defaultsForContribution(contribution, template)
        : {}
      return current
    }, {})
    setWorkflowName(template.workflowName)
    setDomain(template.domain)
    setNodes(templateNodes)
    setValues(templateValues)
    setSelectedNodeId(templateNodes[0]?.id ?? null)
    setInsertIndex(templateNodes.length)
    setInspectorOpen(Boolean(templateNodes.length))
    setActiveStep('graph')
  }

  function removeNode(nodeId: string) {
    setNodes((current) => {
      const next = current.filter((node) => node.id !== nodeId)
      setInsertIndex(Math.min(insertIndex ?? next.length, next.length))
      return next
    })
    setValues((current) => {
      const next = { ...current }
      delete next[nodeId]
      return next
    })
    if (selectedNodeId === nodeId) {
      setSelectedNodeId(null)
      setInspectorOpen(false)
    }
  }

  function moveNode(nodeId: string, direction: -1 | 1) {
    setNodes((current) => {
      const index = current.findIndex((node) => node.id === nodeId)
      const target = index + direction
      if (index < 0 || target < 0 || target >= current.length) return current
      const next = [...current]
      const [node] = next.splice(index, 1)
      next.splice(target, 0, node)
      setInsertIndex(target + 1)
      return next
    })
  }

  function updateNodeField(nodeId: string, field: string, value: string) {
    setValues((current) => ({
      ...current,
      [nodeId]: {
        ...(current[nodeId] ?? {}),
        [field]: value,
      },
    }))
  }

  function buildGraph(): ObservatoryWorkflowGraph {
    return {
      nodes: nodes.map((node) => ({
        id: node.id,
        contribution_id: node.data.contributionId,
        stage: node.data.stage,
        values: values[node.id] ?? {},
      })),
      edges: nodes.slice(0, -1).map((node, index) => ({
        id: `${node.id}-${nodes[index + 1].id}`,
        source: node.id,
        target: nodes[index + 1].id,
      })),
    }
  }

  function generateProposal() {
    setActionMessage(null)
    setProposalLoading(true)
    void createObservatoryWorkflowProposal({
      data: {
        workflow_name: workflowName,
        domain,
        graph: buildGraph(),
      },
    })
      .then((next) => {
        setProposal(next)
        setActiveStep('proposal')
      })
      .catch((error: unknown) => {
        setProposal({
          data: null,
          error:
            error instanceof Error
              ? error.message
              : 'Workflow proposal generation failed.',
        })
        setActiveStep('proposal')
      })
      .finally(() => setProposalLoading(false))
  }

  function runAction(action: ObservatoryWorkflowApplyAction) {
    if (!proposal.data || !action.enabled) return
    void runObservatoryWorkflowAction({
      data: { actionId: action.id, proposal: proposal.data },
    }).then((result) => {
      invalidateCachedResource('observatory:operations')
      setActionMessage(
        result.data?.message ?? result.error ?? 'Action finished',
      )
    })
  }

  return (
    <ObservatoryPage
      description="Compose package-provided workflow steps, configure each stage, preview generated files, then apply guarded actions."
      kicker="Workflows"
      title="New workflow"
    >
      {wizard.error && (
        <div className="phlo-observatory-callout">{wizard.error}</div>
      )}

      <nav className="phlo-workflow-stepper" aria-label="Workflow wizard steps">
        {WORKFLOW_STEPS.map((step, index) => (
          <Button
            alignContent="start"
            block
            className="phlo-workflow-step"
            data-active={activeStep === step.id}
            data-complete={index < activeStepIndex}
            key={step.id}
            onClick={() => setActiveStep(step.id)}
            type="button"
          >
            <span className="phlo-workflow-step-index">{index + 1}</span>
            <span className="phlo-workflow-step-copy">
              <strong>{step.label}</strong>
              <em>{step.description}</em>
            </span>
          </Button>
        ))}
      </nav>

      {activeStep === 'info' && (
        <section className="phlo-observatory-panel phlo-workflow-step-panel">
          <div className="phlo-observatory-panel-header phlo-workflow-card-header">
            <div>
              <h2>Workflow info</h2>
              <p>Set the workflow identity before arranging package steps.</p>
            </div>
            <WandSparkles className="size-4" aria-hidden />
          </div>
          <div className="phlo-workflow-info-grid">
            <label>
              <span>Workflow</span>
              <input
                className="phlo-workflow-input"
                onChange={(event) => setWorkflowName(event.target.value)}
                value={workflowName}
              />
            </label>
            <label>
              <span>Domain</span>
              <input
                className="phlo-workflow-input"
                onChange={(event) => setDomain(event.target.value)}
                value={domain}
              />
            </label>
          </div>
          <div className="phlo-workflow-template-grid">
            {lakehouseTemplates.map((template) => (
              <button
                className="phlo-workflow-template-card"
                key={template.id}
                onClick={() => applyLakehouseTemplate(template)}
                type="button"
              >
                <span>{template.domain}</span>
                <strong>{template.label}</strong>
                <small>{template.summary}</small>
              </button>
            ))}
          </div>
          {(tableResult.error || assetResult.error || qualityResult.error) && (
            <div className="phlo-observatory-panel-footer">
              {tableResult.error ?? assetResult.error ?? qualityResult.error}
            </div>
          )}
          <div className="phlo-workflow-step-actions">
            <Button
              className="phlo-workflow-action"
              onClick={() => setActiveStep('graph')}
              type="button"
              variant="primary"
            >
              Continue to graph
            </Button>
          </div>
        </section>
      )}

      {activeStep === 'graph' && (
        <section className="phlo-workflow-graph-step">
          <div
            className="phlo-workflow-canvas-main"
            data-inspector-open={Boolean(
              inspectorOpen && selectedNode && selectedContribution,
            )}
          >
            <div className="phlo-workflow-canvas-toolbar">
              <Button
                className="phlo-workflow-action"
                disabled={proposalLoading}
                leadingVisual={FileCodeIcon}
                onClick={generateProposal}
                type="button"
                variant="primary"
              >
                {proposalLoading ? 'Generating…' : 'Generate proposal'}
              </Button>
            </div>
            <PipelineLane
              addMenuOpen={addMenuOpen}
              contributions={contributions}
              insertIndex={insertIndex ?? nodes.length}
              nodes={nodes}
              onAddContribution={addContribution}
              onCloseAddMenu={() => setAddMenuOpen(false)}
              onMoveNode={moveNode}
              onRemoveNode={removeNode}
              onSelectInsert={(index) => {
                setInsertIndex(index)
                setAddMenuOpen(true)
              }}
              onSelectNode={(nodeId) => {
                setSelectedNodeId(nodeId)
                setInspectorOpen(true)
              }}
              selectedNodeId={selectedNodeId}
            />
            {inspectorOpen && selectedNode && selectedContribution ? (
              <aside className="phlo-workflow-inspector">
                <Button
                  className="phlo-workflow-inspector-close"
                  onClick={() => setInspectorOpen(false)}
                  size="small"
                  type="button"
                >
                  Close
                </Button>
                <Inspector
                  contribution={selectedContribution}
                  node={selectedNode}
                  onChange={updateNodeField}
                  values={selectedNode ? (values[selectedNode.id] ?? {}) : {}}
                />
              </aside>
            ) : null}
          </div>
        </section>
      )}

      {activeStep === 'proposal' && (
        <section className="phlo-workflow-proposal-step">
          <ReviewPanel
            actionMessage={actionMessage}
            loading={proposalLoading}
            onGenerate={generateProposal}
            onRunAction={runAction}
            proposal={proposal}
          />
        </section>
      )}
    </ObservatoryPage>
  )
}

function PipelineLane({
  nodes,
  contributions,
  insertIndex,
  addMenuOpen,
  selectedNodeId,
  onAddContribution,
  onCloseAddMenu,
  onMoveNode,
  onRemoveNode,
  onSelectInsert,
  onSelectNode,
}: {
  nodes: Array<WorkflowNode>
  contributions: Array<ObservatoryWorkflowWizardContribution>
  insertIndex: number
  addMenuOpen: boolean
  selectedNodeId: string | null
  onAddContribution: (
    contribution: ObservatoryWorkflowWizardContribution,
  ) => void
  onCloseAddMenu: () => void
  onMoveNode: (nodeId: string, direction: -1 | 1) => void
  onRemoveNode: (nodeId: string) => void
  onSelectInsert: (index: number) => void
  onSelectNode: (nodeId: string) => void
}) {
  const groupedContributions = useMemo(
    () =>
      (['source', 'transform', 'quality', 'publish'] as const).map((stage) => ({
        stage,
        items: contributions.filter((item) => item.stage === stage),
      })),
    [contributions],
  )

  return (
    <div className="phlo-workflow-canvas">
      <div className="phlo-workflow-lane" aria-label="Workflow pipeline">
        <InsertPoint
          active={insertIndex === 0}
          index={0}
          onOpenChange={(open) => {
            if (!open) onCloseAddMenu()
          }}
          onSelect={onSelectInsert}
        >
          {addMenuOpen && insertIndex === 0 ? (
            <AddStepMenu
              groupedContributions={groupedContributions}
              insertIndex={insertIndex}
              onAddContribution={onAddContribution}
              onCloseAddMenu={onCloseAddMenu}
            />
          ) : null}
        </InsertPoint>
        {nodes.map((node, index) => (
          <div className="phlo-workflow-lane-item" key={node.id}>
            <PipelineNode
              isFirst={index === 0}
              isLast={index === nodes.length - 1}
              node={node}
              onMoveNode={onMoveNode}
              onRemoveNode={onRemoveNode}
              onSelectNode={onSelectNode}
              selected={selectedNodeId === node.id}
            />
            <InsertPoint
              active={insertIndex === index + 1}
              index={index + 1}
              onOpenChange={(open) => {
                if (!open) onCloseAddMenu()
              }}
              onSelect={onSelectInsert}
            >
              {addMenuOpen && insertIndex === index + 1 ? (
                <AddStepMenu
                  groupedContributions={groupedContributions}
                  insertIndex={insertIndex}
                  onAddContribution={onAddContribution}
                  onCloseAddMenu={onCloseAddMenu}
                />
              ) : null}
            </InsertPoint>
          </div>
        ))}
      </div>
    </div>
  )
}

function AddStepMenu({
  groupedContributions,
  insertIndex,
  onAddContribution,
  onCloseAddMenu,
}: {
  groupedContributions: Array<{
    stage: 'source' | 'transform' | 'quality' | 'publish'
    items: Array<ObservatoryWorkflowWizardContribution>
  }>
  insertIndex: number
  onAddContribution: (
    contribution: ObservatoryWorkflowWizardContribution,
  ) => void
  onCloseAddMenu: () => void
}) {
  return (
    <div className="phlo-workflow-add-menu-surface">
      <div className="phlo-workflow-add-menu-header">
        <div>
          <h2>Add workflow step</h2>
          <p>Step will be inserted at position {insertIndex + 1}.</p>
        </div>
        <Button onClick={onCloseAddMenu} size="small" type="button">
          Close
        </Button>
      </div>
      <div className="phlo-workflow-add-menu-list">
        {groupedContributions.map((group) =>
          group.items.length ? (
            <section key={group.stage}>
              <h3>{STAGE_LABELS[group.stage]}</h3>
              {group.items.map((contribution) => (
                <Button
                  className="phlo-workflow-add-option"
                  key={contribution.id}
                  onClick={() => onAddContribution(contribution)}
                  type="button"
                >
                  <PlusIcon size={16} />
                  <span>
                    <strong>{contribution.label}</strong>
                    <em>{contribution.package}</em>
                  </span>
                </Button>
              ))}
            </section>
          ) : null,
        )}
      </div>
    </div>
  )
}

function InsertPoint({
  active,
  children,
  index,
  onOpenChange,
  onSelect,
}: {
  active: boolean
  children?: ReactNode
  index: number
  onOpenChange: (open: boolean) => void
  onSelect: (index: number) => void
}) {
  return (
    <Popover
      modal={false}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) onOpenChange(false)
      }}
    >
      <div className="phlo-workflow-insert-point" data-active={active}>
        <PopoverTrigger
          aria-label={`Add workflow step at position ${index + 1}`}
          onClick={() => onSelect(index)}
          type="button"
        >
          <span />
          <em>+</em>
          <span />
        </PopoverTrigger>
        {active && children ? (
          <PopoverContent
            align="center"
            className="phlo-workflow-add-menu"
            side="right"
            sideOffset={18}
          >
            {children}
          </PopoverContent>
        ) : null}
      </div>
    </Popover>
  )
}

function PipelineNode({
  node,
  selected,
  isFirst,
  isLast,
  onMoveNode,
  onRemoveNode,
  onSelectNode,
}: {
  node: WorkflowNode
  selected: boolean
  isFirst: boolean
  isLast: boolean
  onMoveNode: (nodeId: string, direction: -1 | 1) => void
  onRemoveNode: (nodeId: string) => void
  onSelectNode: (nodeId: string) => void
}) {
  return (
    <article className="phlo-workflow-canvas-node" data-selected={selected}>
      <button
        className="phlo-workflow-node-select"
        onClick={() => onSelectNode(node.id)}
        type="button"
      >
        <span>{STAGE_LABELS[node.data.stage]}</span>
        <strong>{node.data.label}</strong>
        <p>{node.data.description}</p>
      </button>
      <div className="phlo-workflow-node-footer">
        <em>{node.data.packageName}</em>
        <div className="phlo-workflow-node-actions">
          <IconButton
            aria-label="Move node up"
            className="phlo-workflow-node-action"
            disabled={isFirst}
            icon={ArrowUpIcon}
            onClick={() => onMoveNode(node.id, -1)}
            size="small"
            title="Move up"
            type="button"
            variant="invisible"
          />
          <IconButton
            aria-label="Move node down"
            className="phlo-workflow-node-action"
            disabled={isLast}
            icon={ArrowDownIcon}
            onClick={() => onMoveNode(node.id, 1)}
            size="small"
            title="Move down"
            type="button"
            variant="invisible"
          />
          <IconButton
            aria-label="Remove node"
            className="phlo-workflow-node-action"
            icon={TrashIcon}
            onClick={() => onRemoveNode(node.id)}
            size="small"
            title="Remove"
            type="button"
            variant="invisible"
          />
        </div>
      </div>
    </article>
  )
}

function Inspector({
  node,
  contribution,
  values,
  onChange,
}: {
  node: WorkflowNode | null
  contribution: ObservatoryWorkflowWizardContribution | null | undefined
  values: Record<string, string>
  onChange: (nodeId: string, field: string, value: string) => void
}) {
  if (!node || !contribution) {
    return (
      <div className="phlo-observatory-panel phlo-workflow-inspector-card">
        <div className="phlo-workflow-pane-header">
          <h2>Inspector</h2>
        </div>
        <p>Select a node to configure it.</p>
      </div>
    )
  }

  return (
    <div className="phlo-observatory-panel phlo-workflow-inspector-card">
      <div className="phlo-workflow-pane-header">
        <div>
          <h2>{contribution.label}</h2>
          <p>{contribution.description}</p>
        </div>
        <span className="phlo-observatory-pill">{contribution.package}</span>
      </div>
      <div className="phlo-workflow-inspector-fields">
        {contribution.fields.map((field) => (
          <DynamicField
            field={field}
            key={field.name}
            nodeId={node.id}
            onChange={onChange}
            value={values[field.name] ?? ''}
          />
        ))}
      </div>
    </div>
  )
}

function DynamicField({
  nodeId,
  field,
  value,
  onChange,
}: {
  nodeId: string
  field: ObservatoryWorkflowWizardField
  value: string
  onChange: (nodeId: string, field: string, value: string) => void
}) {
  const common = {
    onChange: (
      event: ChangeEvent<
        HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
      >,
    ) => onChange(nodeId, field.name, event.target.value),
    placeholder: field.description ?? field.label,
    value,
  }
  return (
    <label className="phlo-workflow-field">
      <span>
        {field.label}
        {field.required ? ' *' : ''}
      </span>
      {field.description && <small>{field.description}</small>}
      {field.field_type === 'select' ? (
        <select className="phlo-workflow-input" {...common}>
          {field.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      ) : field.field_type === 'fields' || field.field_type === 'textarea' ? (
        <textarea className="phlo-workflow-input" rows={4} {...common} />
      ) : (
        <input className="phlo-workflow-input" type="text" {...common} />
      )}
    </label>
  )
}

function ReviewPanel({
  proposal,
  actionMessage,
  loading,
  onGenerate,
  onRunAction,
}: {
  proposal: ObservatoryResourceResult<ObservatoryWorkflowProposal>
  actionMessage: string | null
  loading: boolean
  onGenerate: () => void
  onRunAction: (action: ObservatoryWorkflowApplyAction) => void
}) {
  if (proposal.error) {
    return (
      <div className="phlo-observatory-panel phlo-workflow-review-card">
        <div className="phlo-observatory-panel-header phlo-workflow-review-header">
          <div>
            <h2>Review</h2>
            <p>Proposal generation needs attention.</p>
          </div>
        </div>
        <div className="phlo-observatory-panel-footer">{proposal.error}</div>
        <Button
          className="phlo-workflow-action phlo-workflow-apply"
          disabled={loading}
          leadingVisual={FileCodeIcon}
          onClick={onGenerate}
          type="button"
          variant="primary"
        >
          {loading ? 'Generating proposal…' : 'Try again'}
        </Button>
      </div>
    )
  }
  if (!proposal.data) {
    return (
      <div className="phlo-observatory-panel phlo-workflow-review-card">
        <div className="phlo-observatory-panel-header phlo-workflow-review-header">
          <div>
            <h2>Review</h2>
            <p>Generated files and guarded actions appear here.</p>
          </div>
          <FileCodeIcon aria-hidden size={16} />
        </div>
        <div className="phlo-workflow-empty-review">
          {loading
            ? 'Generating a proposal from the graph…'
            : 'Generate a proposal to preview graph-generated files.'}
        </div>
        <div className="phlo-workflow-step-actions">
          <Button
            className="phlo-workflow-action"
            disabled={loading}
            leadingVisual={FileCodeIcon}
            onClick={onGenerate}
            type="button"
            variant="primary"
          >
            {loading ? 'Generating…' : 'Generate proposal'}
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="phlo-observatory-panel phlo-workflow-review-card">
      <div className="phlo-observatory-panel-header phlo-workflow-review-header">
        <div>
          <h2>Review proposal</h2>
          <p>
            {proposal.data.planned_assets.length} lineage{' '}
            {proposal.data.planned_assets.length === 1
              ? 'resource'
              : 'resources'}
            , {proposal.data.planned_models.length} models
          </p>
        </div>
        <span className="phlo-observatory-pill">
          {proposal.data.files.length} files
        </span>
      </div>
      <div className="phlo-workflow-file-list">
        {proposal.data.files.map((file) => (
          <details className="phlo-workflow-file" key={file.path}>
            <summary>
              <FileCodeIcon size={16} />
              <span>{file.path}</span>
              <em>{file.mode}</em>
            </summary>
            <pre>{file.content}</pre>
          </details>
        ))}
      </div>
      {proposal.data.actions.map((action) => (
        <Button
          className="phlo-workflow-action phlo-workflow-apply"
          disabled={loading || !action.enabled}
          key={action.id}
          leadingVisual={CheckCircleIcon}
          onClick={() => onRunAction(action)}
          type="button"
          variant="primary"
        >
          {action.label}
        </Button>
      ))}
      <Button
        className="phlo-workflow-secondary-action"
        disabled={loading}
        onClick={onGenerate}
        type="button"
      >
        {loading ? 'Refreshing proposal…' : 'Refresh proposal'}
      </Button>
      {actionMessage && (
        <div className="phlo-observatory-panel-footer">{actionMessage}</div>
      )}
    </div>
  )
}

function starterGraph(
  contributions: Array<ObservatoryWorkflowWizardContribution>,
) {
  const ids = [
    'dlt.rest-api-source',
    'dbt.transform',
    'pandera.quality-checks',
    'dagster.orchestration',
    'openmetadata.catalog',
  ]
  const contributionsById = new Map(
    contributions.map((contribution) => [contribution.id, contribution]),
  )
  const selected = ids.flatMap((id) => {
    const contribution = contributionsById.get(id)
    return contribution ? [contribution] : []
  })
  const nodes = selected.map((contribution, index) =>
    toCanvasNode(contribution, `node-${index + 1}`),
  )
  return { nodes }
}

function toCanvasNode(
  contribution: ObservatoryWorkflowWizardContribution,
  id: string,
): WorkflowNode {
  return {
    id,
    data: {
      contributionId: contribution.id,
      description: contribution.description,
      label: contribution.label,
      packageName: contribution.package,
      stage: contribution.stage,
    },
  }
}

function starterValues(
  contributions: Array<ObservatoryWorkflowWizardContribution>,
  nodes: Array<WorkflowNode>,
) {
  return nodes.reduce<FormValues>((current, node) => {
    const contribution = contributions.find(
      (item) => item.id === node.data.contributionId,
    )
    current[node.id] = contribution ? defaultsForContribution(contribution) : {}
    return current
  }, {})
}

function defaultsForContribution(
  contribution: ObservatoryWorkflowWizardContribution,
  template?: LakehouseTemplate,
) {
  return contribution.fields.reduce<Record<string, string>>(
    (current, field) => {
      const contextualValue = template
        ? lakehouseFieldValue(contribution.id, field, template)
        : null
      current[field.name] =
        contextualValue ??
        (field.default === undefined || field.default === null
          ? defaultFieldValue(contribution.id, field)
          : String(field.default))
      return current
    },
    {},
  )
}

function defaultFieldValue(
  contributionId: string,
  field: ObservatoryWorkflowWizardField,
  template?: LakehouseTemplate,
) {
  if (template) {
    const context = lakehouseFieldValue(contributionId, field, template)
    if (context !== null) return context
  }
  if (field.name === 'domain') return 'recipes'
  if (field.name === 'table_name') return 'recipes'
  if (field.name === 'unique_key') return 'id'
  if (field.name === 'api_base_url') return 'https://dummyjson.com/recipes'
  if (field.name === 'response_path') return 'recipes'
  if (field.name === 'pagination') return 'none'
  if (field.name === 'auth') return 'none'
  if (field.name === 'cron') return '0 2 * * *'
  if (field.name === 'schedule') return '0 2 * * *'
  if (field.name === 'fields') {
    return 'name:str\ningredients:str\ninstructions:str\nprepTimeMinutes:int\ncookTimeMinutes:int\nservings:int\ndifficulty:str\ncuisine:str\ncaloriesPerServing:int\ntags:str\nrating:float\nreviewCount:int\nmealType:str'
  }
  if (field.name === 'source_name') return 'raw'
  if (field.name === 'source_stream') return 'public.recipes'
  if (field.name === 'target_table') return 'recipes'
  if (field.name === 'primary_key') return 'id'
  if (field.name === 'replication_mode') return 'incremental'
  if (field.name === 'update_key') return 'updated_at'
  if (field.name === 'project_name') return 'recipe_catalog'
  if (field.name === 'source_table') return 'recipes'
  if (field.name === 'staging_model_name') return 'stg_recipes'
  if (field.name === 'staging_source_relation') return 'raw.recipes'
  if (field.name === 'enable_rename') return 'no'
  if (field.name === 'enable_cast') return 'no'
  if (field.name === 'enable_aggregate') return 'no'
  if (contributionId === 'dbt.basic-model' && field.name === 'model_name')
    return 'stg_recipes'
  if (contributionId === 'dbt.basic-model' && field.name === 'source_relation')
    return 'raw.recipes'
  if (contributionId === 'dbt.source-yml' && field.name === 'source_name')
    return 'raw'
  if (contributionId === 'dbt.schema-tests' && field.name === 'model_name')
    return 'clean_recipes'
  if (contributionId === 'dbt.filter-rows' && field.name === 'model_name')
    return 'filtered_recipes'
  if (contributionId === 'dbt.filter-rows' && field.name === 'source_relation')
    return "ref('stg_recipes')"
  if (contributionId === 'dbt.filter-rows' && field.name === 'where')
    return 'rating >= 4.5'
  if (contributionId === 'dbt.transform' && field.name === 'where')
    return 'rating >= 4.5'
  if (field.name === 'filter_model_name') return 'filtered_recipes'
  if (field.name === 'dedupe_model_name') return 'clean_recipes'
  if (field.name === 'test_model_name') return 'clean_recipes'
  if (contributionId === 'dbt.deduplicate' && field.name === 'model_name')
    return 'clean_recipes'
  if (contributionId === 'dbt.deduplicate' && field.name === 'source_relation')
    return "ref('filtered_recipes')"
  if (field.name === 'partition_by') return 'id'
  if (field.name === 'order_by') return 'reviewCount'
  if (field.name === 'renames')
    return 'name:recipe_name\ncaloriesPerServing:calories_per_serving'
  if (field.name === 'casts') return 'rating:double\nreviewCount:integer'
  if (field.name === 'group_by') return 'cuisine'
  if (field.name === 'metrics')
    return 'recipe_count:count(*)\navg_rating:avg(rating)'
  if (field.name === 'check_name') return 'clean_recipes_quality'
  if (field.name === 'not_null_columns') return 'id\nname\ncuisine'
  if (field.name === 'range_checks') return 'rating:0:5\nreviewCount:0:100000'
  if (field.name === 'freshness_column') return 'updated_at'
  if (field.name === 'freshness_hours') return '24'
  if (field.name === 'min_rows') return '1'
  if (field.name === 'job_name') return 'recipe_catalog_job'
  if (field.name === 'asset_group') return 'recipes'
  if (field.name === 'include_sensor') return 'no'
  if (field.name === 'service_name') return 'phlo'
  if (field.name === 'database') return 'warehouse'
  if (field.name === 'schema') return 'recipes'
  if (field.name === 'owner') return 'data-platform'
  if (field.name === 'tags') return 'domain.recipes\nsource.dummyjson'
  if (field.name === 'description')
    return 'Dataset metadata for the recipe workflow generated from DummyJSON recipes.'
  if (field.name === 'source_relation') return "ref('clean_recipes')"
  if (field.name === 'model_name') return 'recipe_model'
  return ''
}

function buildLakehouseTemplates(
  tables: Array<ObservatoryTable>,
  assets: Array<ObservatoryAsset>,
  quality: Array<ObservatoryQualityCheck>,
): Array<LakehouseTemplate> {
  const focusTable =
    tables.find(
      (table) =>
        (table.namespace ?? '').toLowerCase() === 'gold' &&
        tableCatalogState(table) === 'queryable',
    ) ??
    tables.find((table) => (table.namespace ?? '').toLowerCase() === 'gold') ??
    tables.find((table) => tableCatalogState(table) === 'queryable') ??
    tables[0] ??
    null
  const focusAsset =
    assets.find((asset) => asset.id === focusTable?.asset_id) ??
    assets.find((asset) => inferAssetStage(asset) === 'gold') ??
    assets[0] ??
    null
  const domain = inferDomain(focusTable, focusAsset)
  const focusChecks = quality.filter(
    (check) => check.asset_id === focusAsset?.id,
  )
  const hasObservedLakehouse = tables.length > 0 || assets.length > 0

  if (!hasObservedLakehouse) {
    return [
      {
        id: 'starter-observe',
        label: 'Observe current lakehouse',
        summary:
          'Create a source, transform, quality, and Dataset readiness workflow.',
        workflowName: 'lakehouse_observability',
        domain: 'lakehouse',
        focusTable: null,
        focusAsset: null,
        quality: [],
        contributionIds: [
          'dlt.rest-api-source',
          'dbt.transform',
          'pandera.quality-checks',
          'dagster.orchestration',
          'openmetadata.catalog',
        ],
      },
    ]
  }

  return [
    {
      id: 'govern-gold-table',
      label: `Govern ${focusTable?.name ?? focusAsset?.name ?? 'gold table'}`,
      summary: `${focusChecks.length} checks observed; generate tests, orchestration, and Dataset metadata.`,
      workflowName: `${domain}_governed_release`,
      domain,
      focusTable,
      focusAsset,
      quality: focusChecks,
      contributionIds: [
        'sling.replication-source',
        'dbt.transform',
        'pandera.quality-checks',
        'dagster.orchestration',
        'openmetadata.catalog',
      ],
    },
    {
      id: 'publish-serving',
      label: 'Publish serving surface',
      summary:
        'Start from the active table and produce Dataset/API-facing metadata.',
      workflowName: `${domain}_serving_dataset`,
      domain,
      focusTable,
      focusAsset,
      quality: focusChecks,
      contributionIds: [
        'sling.replication-source',
        'dbt.transform',
        'dagster.orchestration',
        'openmetadata.catalog',
      ],
    },
    {
      id: 'new-source-to-quality',
      label: 'Add source with quality gate',
      summary:
        'Use observed field names as defaults for a new ingested source.',
      workflowName: `${domain}_source_quality`,
      domain,
      focusTable,
      focusAsset,
      quality: focusChecks,
      contributionIds: [
        'dlt.rest-api-source',
        'dbt.transform',
        'pandera.quality-checks',
        'dagster.orchestration',
      ],
    },
  ]
}

function lakehouseFieldValue(
  contributionId: string,
  field: ObservatoryWorkflowWizardField,
  template: LakehouseTemplate,
): string | null {
  const table = template.focusTable
  const asset = template.focusAsset
  const tableName = table?.name ?? asset?.name ?? template.domain
  const relation = table?.id ?? `${template.domain}.${tableName}`
  const idColumns = readStringList(table?.metadata.id_columns)
  const primaryKey =
    idColumns[0] ??
    inferPrimaryKeyFromName(tableName) ??
    (template.domain === 'keystone' ? 'export_id' : 'id')
  const sourceRelation = relation.includes('.')
    ? relation
    : `${template.domain}.${relation}`
  const cleanModel = tableName.startsWith('clean_')
    ? tableName
    : `clean_${tableName}`
  const columns = tableColumnProfiles(table)
  const columnNames = columns.map((column) => column.name)
  const numericColumnNames = columns
    .filter((column) =>
      /int|double|float|decimal|numeric|bigint/i.test(column.type),
    )
    .map((column) => column.name)
  const timestampColumn =
    columnNames.find((name) =>
      /updated_at|created_at|timestamp|_at$/i.test(name),
    ) ??
    readString(table?.metadata.updated_at) ??
    null
  const groupColumn =
    columnNames.find((name) =>
      /assay|type|group|category|status/i.test(name),
    ) ??
    columnNames.find((name) => name !== primaryKey) ??
    primaryKey
  const metricColumn =
    numericColumnNames.find((name) => !/_id$/i.test(name)) ??
    numericColumnNames[0] ??
    null

  if (field.name === 'domain') return template.domain
  if (field.name === 'source_name') return 'DUCKDB'
  if (field.name === 'project_name') return template.workflowName
  if (field.name === 'table_name') return tableName
  if (field.name === 'target_table') return tableName
  if (field.name === 'source_table') return tableName
  if (field.name === 'source_stream') return sourceRelation
  if (field.name === 'source_relation') return sourceRelation
  if (field.name === 'staging_source_relation') return sourceRelation
  if (field.name === 'staging_model_name') return `stg_${tableName}`
  if (field.name === 'filter_model_name') return `filtered_${tableName}`
  if (field.name === 'dedupe_model_name') return cleanModel
  if (field.name === 'test_model_name') return cleanModel
  if (field.name === 'model_name') {
    return contributionId === 'dbt.basic-model'
      ? `stg_${tableName}`
      : cleanModel
  }
  if (field.name === 'unique_key') return primaryKey
  if (field.name === 'primary_key') return primaryKey
  if (field.name === 'partition_by') return primaryKey
  if (field.name === 'order_by') return timestampColumn ?? primaryKey
  if (field.name === 'update_key') return timestampColumn ?? primaryKey
  if (field.name === 'fields') {
    return columns.length
      ? columns
          .map((column) => `${column.name}:${dbTypeToFieldType(column.type)}`)
          .join('\n')
      : `${primaryKey}:str`
  }
  if (field.name === 'renames') return ''
  if (field.name === 'casts') {
    return columns
      .filter((column) => column.type)
      .slice(0, 8)
      .map((column) => `${column.name}:${dbTypeToCastType(column.type)}`)
      .join('\n')
  }
  if (field.name === 'where') {
    return metricColumn ? `${metricColumn} >= 0` : `${primaryKey} is not null`
  }
  if (field.name === 'group_by') return groupColumn
  if (field.name === 'metrics') {
    return metricColumn
      ? `${tableName}_rows:count(*)\navg_${metricColumn}:avg(${metricColumn})`
      : `${tableName}_rows:count(*)`
  }
  if (field.name === 'check_name') return `${tableName}_quality`
  if (field.name === 'not_null_columns') {
    return [
      primaryKey,
      ...requiredColumnsForQuality(template.quality),
      ...columnNames,
    ]
      .filter(Boolean)
      .slice(0, 6)
      .join('\n')
  }
  if (field.name === 'range_checks')
    return rangeChecksForDomain(template.domain)
  if (field.name === 'freshness_column') return timestampColumn ?? ''
  if (field.name === 'min_rows') {
    const rows = table?.metadata.rows ?? table?.metadata.records
    return typeof rows === 'number' && rows > 0
      ? String(Math.min(rows, 1000))
      : '1'
  }
  if (field.name === 'job_name') return `${template.workflowName}_job`
  if (field.name === 'asset_group') return asset?.group ?? template.domain
  if (field.name === 'schema') return table?.namespace ?? template.domain
  if (field.name === 'owner')
    return readString(asset?.metadata.owner) ?? 'data-platform'
  if (field.name === 'tags') {
    return [
      `domain.${template.domain}`,
      table?.namespace ? `stage.${table.namespace}` : null,
    ]
      .filter(Boolean)
      .join('\n')
  }
  if (field.name === 'description') {
    return `Dataset metadata for ${tableName}, generated from the observed ${template.domain} lakehouse.`
  }
  return null
}

function tableColumnProfiles(
  table: ObservatoryTable | null,
): Array<{ name: string; type: string }> {
  const columns = table?.metadata.columns
  if (!Array.isArray(columns)) return []
  return columns.flatMap((column) => {
    if (
      typeof column === 'object' &&
      column !== null &&
      'name' in column &&
      typeof column.name === 'string'
    ) {
      return [
        {
          name: column.name,
          type:
            'type' in column && typeof column.type === 'string'
              ? column.type
              : 'varchar',
        },
      ]
    }
    return []
  })
}

function dbTypeToFieldType(type: string): string {
  const lower = type.toLowerCase()
  if (/int|bigint|smallint/.test(lower)) return 'int'
  if (/double|float|decimal|numeric|real/.test(lower)) return 'float'
  if (/bool/.test(lower)) return 'bool'
  if (/date|time/.test(lower)) return 'datetime'
  return 'str'
}

function dbTypeToCastType(type: string): string {
  const lower = type.toLowerCase()
  if (/bigint/.test(lower)) return 'bigint'
  if (/int|smallint/.test(lower)) return 'integer'
  if (/double|float|decimal|numeric|real/.test(lower)) return 'double'
  if (/bool/.test(lower)) return 'boolean'
  if (/timestamp/.test(lower)) return 'timestamp'
  if (/date/.test(lower)) return 'date'
  return 'varchar'
}

function tableCatalogState(table: ObservatoryTable): string {
  const state = String(table.metadata.catalog_state ?? '').toLowerCase()
  if (state === 'queryable') return 'queryable'
  if (table.metadata.catalog_present === true) return 'queryable'
  return 'registered'
}

function inferDomain(
  table: ObservatoryTable | null,
  asset: ObservatoryAsset | null,
): string {
  const raw = [
    table?.id,
    table?.name,
    table?.namespace,
    asset?.id,
    asset?.group,
    asset?.metadata.domain,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
  if (raw.includes('keystone')) return 'keystone'
  const namespace = table?.namespace?.toLowerCase()
  if (namespace && !['gold', 'silver', 'bronze', 'raw'].includes(namespace)) {
    return namespace.replace(/[^a-z0-9_]+/g, '_')
  }
  return 'lakehouse'
}

function inferAssetStage(asset: ObservatoryAsset): string {
  const raw = [asset.group, asset.id, asset.name, asset.metadata.stage]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
  if (raw.includes('gold') || raw.includes('analytics')) return 'gold'
  if (raw.includes('silver')) return 'silver'
  if (raw.includes('bronze') || raw.includes('raw')) return 'bronze'
  if (raw.includes('serving')) return 'serving'
  return 'lineage'
}

function inferPrimaryKeyFromName(name: string): string | null {
  const lower = name.toLowerCase()
  if (lower.includes('export')) return 'export_id'
  if (lower.includes('experiment')) return 'experiment_id'
  if (lower.includes('plate')) return 'plate_id'
  if (lower.includes('sample')) return 'sample_id'
  return null
}

function requiredColumnsForQuality(
  quality: Array<ObservatoryQualityCheck>,
): Array<string> {
  return quality.flatMap((check) => readStringList(check.metadata.columns))
}

function rangeChecksForDomain(domain: string): string {
  if (domain === 'keystone') {
    return 'total_records:0:100000000\nfailed_records:0:1000000\nquality_score:0:1'
  }
  return 'row_count:1:100000000'
}

function readString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function readStringList(value: unknown): Array<string> {
  if (Array.isArray(value)) {
    return value.filter((item): item is string => typeof item === 'string')
  }
  if (typeof value === 'string' && value.trim()) return [value]
  return []
}
