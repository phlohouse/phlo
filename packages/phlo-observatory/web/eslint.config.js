/**
 * ESLint flat config for the Observatory web app.
 *
 * Uses the shared TanStack config (typescript-eslint + react hooks rules) which
 * matches the rest of the Phlo TypeScript surface.
 */
import { tanstackConfig } from "@tanstack/eslint-config";

export default [
  ...tanstackConfig,
  {
    ignores: ["dist/**", "src/routeTree.gen.ts", "eslint.config.js"],
  },
];
