/**
 * Settings placeholder pending Stage 1 build.
 */
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/settings')({
  component: RouteComponent,
})

function RouteComponent() {
  return <div>Hello "/settings"!</div>
}
