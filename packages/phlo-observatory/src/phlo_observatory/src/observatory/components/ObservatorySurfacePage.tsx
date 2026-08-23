/**
 * Generic list page for an observatory surface: renders items as selectable
 * rows with empty and error states, plus an inspector aside naming the
 * backing data contract.
 */
import { Layers3 } from 'lucide-react'

import type {
  ObservatoryResourceResult,
  ObservatorySurfaceItem,
} from '@/observatory/api/types'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'

export function ObservatorySurfacePage({
  contract,
  description,
  emptyCopy,
  error,
  items,
  kicker,
  title,
}: {
  contract: string
  description: string
  emptyCopy: string
  error: ObservatoryResourceResult<Array<ObservatorySurfaceItem>>['error']
  items: Array<ObservatorySurfaceItem>
  kicker: string
  title: string
}) {
  return (
    <ObservatoryPage
      kicker={kicker}
      title={title}
      description={description}
      action={
        <span className="phlo-observatory-pill">{items.length} available</span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-surface-shell">
        <div className="phlo-observatory-command-primary phlo-observatory-surface-list">
          <div className="phlo-observatory-list">
            {items.map((item) => (
              <SurfaceRow item={item} key={item.id} />
            ))}
            {items.length === 0 && error && (
              <div className="phlo-observatory-operation-empty">
                <div>
                  <span className="phlo-observatory-inspector-label">
                    Unable to load
                  </span>
                  <h2>{title} could not load.</h2>
                  <p>{error}</p>
                </div>
                <div className="phlo-observatory-detail-list">
                  <div className="phlo-observatory-mini-row">
                    <span>Source</span>
                    <small>{contract}</small>
                  </div>
                  <div className="phlo-observatory-mini-row">
                    <span>Status</span>
                    <small>error</small>
                  </div>
                </div>
              </div>
            )}
            {items.length === 0 && !error && (
              <div className="phlo-observatory-operation-empty">
                <div>
                  <span className="phlo-observatory-inspector-label">
                    Nothing connected
                  </span>
                  <h2>{title} has no items yet.</h2>
                  <p>{emptyCopy}</p>
                </div>
                <div className="phlo-observatory-detail-list">
                  <div className="phlo-observatory-mini-row">
                    <span>Connection</span>
                    <small>waiting for provider data</small>
                  </div>
                  <div className="phlo-observatory-mini-row">
                    <span>Available</span>
                    <small>0</small>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
          <div className="phlo-observatory-inspector-label">Coverage</div>
          <h2>{kicker}</h2>
          <p>{description}</p>
          <dl className="phlo-observatory-facts">
            <dt>Source</dt>
            <dd>{contract}</dd>
            <dt>Available</dt>
            <dd>{items.length}</dd>
          </dl>
          {error && items.length > 0 && (
            <div className="phlo-observatory-panel-footer">{error}</div>
          )}
        </aside>
      </section>
    </ObservatoryPage>
  )
}

function SurfaceRow({ item }: { item: ObservatorySurfaceItem }) {
  return (
    <div className="phlo-observatory-timeline-row">
      <span className="phlo-observatory-dot" data-state={item.health.state} />
      <div>
        <div className="phlo-observatory-row-title">
          <Layers3 className="size-4" />
          {item.name}
        </div>
        <div className="phlo-observatory-row-meta">
          {[item.kind, item.summary].filter(Boolean).join(' · ')}
        </div>
      </div>
      <span className="phlo-observatory-pill">{item.health.state}</span>
    </div>
  )
}
