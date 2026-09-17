/**
 * Releases placeholder pending Stage 1 build.
 */
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/releases')({
  component: RouteComponent,
})

function RouteComponent() {
  return <div>Hello "/releases"!</div>
}
