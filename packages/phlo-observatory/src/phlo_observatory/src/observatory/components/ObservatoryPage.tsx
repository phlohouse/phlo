/**
 * Shared page shell for observatory sections: kicker breadcrumbs (linked when
 * the kicker maps to a section route), title, description, and an optional
 * action slot.
 */
import { Link } from '@tanstack/react-router'
import { ChevronRight } from 'lucide-react'
import type { ReactNode } from 'react'

const sectionHref: Record<string, string> = {
  Data: '/datasets',
  Dataset: '/datasets',
  Tables: '/tables',
  Operations: '/operations',
  Logs: '/logs',
  Quality: '/quality',
  Governance: '/governance',
  Publishing: '/publishing',
  Platform: '/services',
  Workspace: '/workspace',
}

export function ObservatoryPage({
  kicker,
  title,
  description,
  action,
  children,
}: {
  kicker: string
  title: string
  description: string
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="phlo-observatory-content">
      <header className="phlo-observatory-section-header">
        <div>
          <nav aria-label="Breadcrumb" className="phlo-observatory-breadcrumbs">
            {sectionHref[kicker] ? (
              <Link to={sectionHref[kicker]}>{kicker}</Link>
            ) : (
              <span>{kicker}</span>
            )}
            <ChevronRight aria-hidden="true" className="size-3" />
            <span aria-current="page">{title}</span>
          </nav>
          <h1 className="phlo-observatory-title">{title}</h1>
          <p className="phlo-observatory-subtitle">{description}</p>
        </div>
        {action}
      </header>
      {children}
    </div>
  )
}
