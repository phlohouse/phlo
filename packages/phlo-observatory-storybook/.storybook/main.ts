/**
 * Storybook config for the observatory component gallery; aliases web sources and forces automatic JSX.
 */
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import type { StorybookConfig } from "@storybook/react-vite";

const webSrc = path.resolve(__dirname, "../../phlo-observatory/web/src");

const config: StorybookConfig = {
  stories: ["../src/stories/**/*.stories.@(ts|tsx)"],
  addons: ["@storybook/addon-essentials"],
  framework: { name: "@storybook/react-vite", options: {} },
  viteFinal: async (config) => {
    config.resolve = config.resolve ?? {};
    config.resolve.alias = { ...(config.resolve.alias ?? {}), "@": webSrc };
    config.plugins = [...(config.plugins ?? []), react({ jsxRuntime: "automatic" }), tailwindcss()];
    return config;
  },
};
export default config;
