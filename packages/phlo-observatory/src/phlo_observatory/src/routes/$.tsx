/**
 * Catch-all route. Every unmatched path lands here and is converted into a
 * 404 render instead of a router error.
 */
import { Link, createFileRoute, notFound } from '@tanstack/react-router'

import { buttonVariants } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/$')({
  beforeLoad: () => {
    throw notFound({ routeId: '__root__' })
  },
  component: ObservatoryNotFoundRoute,
})

function ObservatoryNotFoundRoute() {
  return (
    <div className="phlo-observatory-content">
      <section className="phlo-observatory-panel phlo-observatory-empty-panel">
        <h1 className="phlo-observatory-title">Page not found</h1>
        <p className="phlo-observatory-subtitle">
          This Observatory surface is not available.
        </p>
        <Link to="/" className={cn(buttonVariants({ size: 'sm' }))}>
          Go Home
        </Link>
      </section>
    </div>
  )
}
