/** Configures Vite, TanStack Start, React, Tailwind, and path aliases. */
import { defineConfig } from 'vite'
import { tanstackStart } from '@tanstack/react-start/plugin/vite'
import viteReact from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { nitro } from 'nitro/vite'

export default defineConfig({
  server: { port: 3000 },
  resolve: { tsconfigPaths: true },
  plugins: [
    tailwindcss(),
    tanstackStart({
      router: {
        routeTreeFileHeader: [
          '// phlo: no-header',
          // reason: TanStack generates this route registry; lint its source routes instead.
          '/* eslint-disable */',
          '// @ts-nocheck',
        ],
      },
    }), // must come before react()
    nitro({ preset: 'node-server' }),
    viteReact(),
  ],
})
