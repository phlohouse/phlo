/**
 * Vite config for the Observatory TanStack Start app with Tailwind and tsconfig paths.
 */
import tailwindcss from "@tailwindcss/vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import viteReact from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import viteTsConfigPaths from "vite-tsconfig-paths";

const phloApiUrl = process.env.PHLO_API_URL ?? "http://localhost:4000";

const config = defineConfig({
  server: {
    port: 3001,
    host: "localhost",
    proxy: {
      "/api/observatory": {
        target: phloApiUrl,
        changeOrigin: true,
      },
    },
  },
  plugins: [
    viteTsConfigPaths({ projects: ["./tsconfig.json"] }),
    tailwindcss(),
    tanstackStart(),
    viteReact(),
  ],
});

export default config;
