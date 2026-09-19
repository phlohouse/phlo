/**
 * Production entrypoint for the built Observatory application.
 *
 * `vite preview` is a development artifact — it does not run the TanStack
 * Start server bundle or the `/api/observatory` proxy route. This launcher
 * serves the compiled fetch handler through srvx's Node adapter, with the
 * built client assets mounted first, on the configured HOST/PORT.
 */
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { serve } from "srvx/node";
import { serveStatic } from "srvx/static";
import { log } from "srvx/log";
import entry from "./dist/server/server.js";

const here = dirname(fileURLToPath(import.meta.url));
const clientDir = join(here, "dist", "client");

const port = Number(process.env.PORT ?? 3000);
const hostname = process.env.HOST ?? "0.0.0.0";

const fetchHandler = entry?.fetch ?? entry?.default?.fetch;
if (typeof fetchHandler !== "function") {
  console.error(
    "dist/server/server.js does not export a fetch handler — run `npm run build` first",
  );
  process.exit(1);
}

serve({
  fetch: fetchHandler,
  middleware: [log(), serveStatic({ dir: clientDir })],
  port,
  hostname,
  gracefulShutdown: true,
});
