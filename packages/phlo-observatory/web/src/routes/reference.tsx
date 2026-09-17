/**
 * Reference placeholder pending Stage 1 build.
 */
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/reference')({
  component: RouteComponent,
})

function RouteComponent() {
  return <div>Hello "/reference"!</div>
}
