/**
 * Vite config for the Observatory TanStack Start app: dev-server host and
 * HMR overrides, tsconfig path aliases, Tailwind, and nitro output.
 */
import tailwindcss from '@tailwindcss/vite'
import { devtools } from '@tanstack/devtools-vite'
import { tanstackStart } from '@tanstack/react-start/plugin/vite'
import viteReact from '@vitejs/plugin-react'
import { nitro } from 'nitro/vite'
import { defineConfig } from 'vite'
import viteTsConfigPaths from 'vite-tsconfig-paths'

const devHost = process.env.DEV_HOST ?? 'localhost'
const devHmrHost = process.env.DEV_HMR_HOST ?? devHost
const devHmrClientPort = process.env.DEV_HMR_CLIENT_PORT
const devAllowedHosts = (process.env.DEV_ALLOWED_HOSTS ?? devHost)
  .split(',')
  .flatMap((host) => {
    const trimmed = host.trim()
    return trimmed ? [trimmed] : []
  })
const phloApiUrl = process.env.PHLO_API_URL ?? 'http://localhost:4000'

const config = defineConfig({
  server: {
    port: 3001,
    allowedHosts: devAllowedHosts,
    host: devHost,
    hmr: {
      host: devHmrHost,
      ...(devHmrClientPort
        ? { clientPort: Number.parseInt(devHmrClientPort, 10) }
        : {}),
      protocol: 'ws',
    },
  },
  ssr: {
    noExternal: ['@primer/react'],
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('@tanstack/')) return 'vendor-tanstack'
          if (
            id.includes('/node_modules/react/') ||
            id.includes('/node_modules/react-dom/')
          ) {
            return 'vendor-react'
          }
          return undefined
        },
      },
    },
  },
  plugins: [
    devtools(),
    nitro({
      routeRules: {
        '/api/observatory/**': {
          proxy: `${phloApiUrl}/api/observatory/**`,
        },
      },
    }),
    // this is the plugin that enables path aliases
    viteTsConfigPaths({
      projects: ['./tsconfig.json'],
    }),
    tailwindcss(),
    tanstackStart(),
    viteReact(),
  ],
})

export default config
