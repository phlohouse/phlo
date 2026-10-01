/** Constructs the client router from the generated route tree. */
import { createRouter } from '@tanstack/react-router'
import { routeTree } from './routeTree.gen'
import { NotFound, RouteError } from '@/components/phlo/states'

export function getRouter() {
  return createRouter({
    routeTree,
    scrollRestoration: true,
    defaultPreload: 'intent',
    defaultNotFoundComponent: NotFound,
    defaultErrorComponent: RouteError,
  })
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof getRouter>
  }
}
