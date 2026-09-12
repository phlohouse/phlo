/**
 * Root layout. Wires React Query, extension, and settings providers around
 * the Observatory shell, injects the runtime API URL bootstrap script, and
 * renders the app-wide not-found page.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  HeadContent,
  Link,
  Outlet,
  Scripts,
  createRootRoute,
} from '@tanstack/react-router'
import * as React from 'react'

import appCss from '../styles.css?url'
import { ObservatoryExtensionProvider } from '@/extensions/registry'
import { ObservatorySettingsProvider } from '@/hooks/useObservatorySettings'
import { buttonVariants } from '@/components/ui/button'
import { Toaster } from '@/components/ui/toaster'
import { AppShell } from '@/components/shell/app-shell'
import { OBSERVATORY_THEME_STORAGE_KEY } from '@/observatory/shell/theme'
import { cn } from '@/lib/utils'

if (typeof window !== 'undefined') {
  ;(
    globalThis as typeof globalThis & { __phloReact?: typeof React }
  ).__phloReact = React
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60,
      retry: 1,
    },
  },
})

export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: 'utf-8' },
      { name: 'viewport', content: 'width=device-width, initial-scale=1' },
      { title: 'Phlo Observatory' },
      {
        name: 'description',
        content: 'Mission control for the lakehouse',
      },
    ],
    links: [
      { rel: 'stylesheet', href: appCss },
      { rel: 'icon', href: '/favicon.ico' },
    ],
  }),

  component: RootLayout,
  notFoundComponent: NotFound,
})

const THEME_BOOTSTRAP = `;(() => {
  try {
    var mode = window.localStorage.getItem('${OBSERVATORY_THEME_STORAGE_KEY}');
    var systemDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    var dark = mode === 'dark' || (mode !== 'light' && systemDark);
    document.documentElement.classList.toggle('dark', dark);
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
  } catch (_) {}
})();`

function runtimeBrowserApiUrl() {
  return typeof process !== 'undefined'
    ? process.env.PHLO_API_BROWSER_URL || ''
    : ''
}

function runtimeBootstrapScript() {
  const browserApiUrl = runtimeBrowserApiUrl()
  return `;(() => {
    window.__PHLO_API_BROWSER_URL__ = ${JSON.stringify(browserApiUrl)};
  })();`
}

function RootLayout() {
  const browserApiUrl = runtimeBrowserApiUrl()

  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <meta name="phlo-api-browser-url" content={browserApiUrl} />
        <script
          suppressHydrationWarning
          dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }}
        />
        <script
          suppressHydrationWarning
          dangerouslySetInnerHTML={{ __html: runtimeBootstrapScript() }}
        />
        <HeadContent />
      </head>
      <body className="bg-background text-foreground min-h-svh">
        <QueryClientProvider client={queryClient}>
          <ObservatorySettingsProvider>
            <ObservatoryExtensionProvider>
              <AppShell>
                <Outlet />
              </AppShell>
            </ObservatoryExtensionProvider>
          </ObservatorySettingsProvider>
        </QueryClientProvider>
        <Toaster />
        <Scripts />
      </body>
    </html>
  )
}

function NotFound() {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="max-w-sm text-center">
        <h1 className="text-foreground text-lg font-semibold">
          Page not found
        </h1>
        <p className="text-muted-foreground mt-1 text-xs">
          This Observatory surface does not exist.
        </p>
        <Link
          className={cn(buttonVariants({ size: 'sm' }), 'mt-4 inline-flex')}
          to="/"
        >
          Back to overview
        </Link>
      </div>
    </div>
  )
}
