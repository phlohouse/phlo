/**
 * Root layout. Wires theme, React Query, extension, and settings providers
 * around the Observatory shell, injects the runtime API URL bootstrap
 * script, and renders the app-wide not-found page.
 */
import { BaseStyles, ThemeProvider } from '@primer/react'
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
import observatoryLedgerCss from '../observatory/design/ledger.css?url'
import { ObservatoryExtensionProvider } from '@/extensions/registry'
import { ObservatorySettingsProvider } from '@/hooks/useObservatorySettings'
import { cn } from '@/lib/utils'
import { buttonVariants } from '@/components/ui/button'
import { Toaster } from '@/components/ui/toaster'
import { ObservatoryShell } from '@/observatory/shell/ObservatoryShell'
import { OBSERVATORY_THEME_STORAGE_KEY } from '@/observatory/shell/theme'

const observatoryLedgerHref = `${observatoryLedgerCss}?v=20260711-nav-alignment`

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
        content: 'Unified visibility into your data platform',
      },
    ],
    links: [
      { rel: 'stylesheet', href: appCss },
      { rel: 'stylesheet', href: observatoryLedgerHref },
      { rel: 'icon', href: '/favicon.ico' },
    ],
  }),

  component: RootLayout,
  notFoundComponent: NotFound,
})

const OBSERVATORY_THEME_BOOTSTRAP = `;(() => {
  try {
    var mode = window.localStorage.getItem('${OBSERVATORY_THEME_STORAGE_KEY}');
    var systemDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    var dark = mode === 'dark' || (mode !== 'light' && systemDark);
    document.documentElement.dataset.phloObservatoryRoute = 'true';
    document.documentElement.dataset.phloObservatoryTheme = dark ? 'dark' : 'light';
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
    <html lang="en" className="" suppressHydrationWarning>
      <head>
        <meta name="phlo-api-browser-url" content={browserApiUrl} />
        <script
          suppressHydrationWarning
          dangerouslySetInnerHTML={{ __html: OBSERVATORY_THEME_BOOTSTRAP }}
        />
        <script
          suppressHydrationWarning
          dangerouslySetInnerHTML={{ __html: runtimeBootstrapScript() }}
        />
        <HeadContent />
      </head>
      <body className="phlo-observatory-document min-h-svh bg-background text-foreground">
        <ThemeProvider
          colorMode="auto"
          dayScheme="light"
          nightScheme="dark"
          preventSSRMismatch
        >
          <BaseStyles className="phlo-primer-base">
            <QueryClientProvider client={queryClient}>
              <ObservatorySettingsProvider>
                <ObservatoryExtensionProvider>
                  <ObservatoryShell>
                    <Outlet />
                  </ObservatoryShell>
                </ObservatoryExtensionProvider>
              </ObservatorySettingsProvider>
            </QueryClientProvider>
          </BaseStyles>
        </ThemeProvider>
        <Toaster />
        <Scripts />
      </body>
    </html>
  )
}

function NotFound() {
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
