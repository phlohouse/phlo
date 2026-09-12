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
    <div className="flex min-h-0 flex-1 items-center justify-center p-6">
      <section className="bg-sheet border-rule flex max-w-md flex-col items-center gap-2 px-8 py-10 text-center border">
        <h1 className="text-foreground text-lg font-semibold tracking-tight">
          Page not found
        </h1>
        <p className="text-muted-foreground text-xs/relaxed">
          This Observatory surface is not available.
        </p>
        <Link to="/" className={cn(buttonVariants({ size: 'sm' }), 'mt-3')}>
          Go Home
        </Link>
      </section>
    </div>
  )
}
