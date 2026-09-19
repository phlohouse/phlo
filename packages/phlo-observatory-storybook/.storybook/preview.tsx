/**
 * Storybook preview: loads the app token layer and provides the theme toggle
 * and tooltip provider the components expect.
 */
import { useEffect, useState } from "react";
import type { Decorator, Preview } from "@storybook/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TooltipProvider } from "../../phlo-observatory/web/src/components/ui/tooltip";
import "./preview.css";

const withTheme: Decorator = (Story, context) => {
  const theme = (context.globals.theme as string) ?? "light";
  // Gallery queries must never hit the network — they stay disabled or fail fast.
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: false, enabled: false } },
      }),
  );
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    document.documentElement.style.colorScheme = theme;
  }, [theme]);
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <div style={{ background: "var(--background)", padding: 24, minHeight: "100vh" }}>
          <Story />
        </div>
      </TooltipProvider>
    </QueryClientProvider>
  );
};

const preview: Preview = {
  decorators: [withTheme],
  globalTypes: {
    theme: {
      description: "Mission Control theme",
      toolbar: {
        title: "Theme",
        icon: "circlehollow",
        items: [
          { value: "light", title: "Light" },
          { value: "dark", title: "Dark" },
        ],
        dynamicTitle: true,
      },
    },
  },
  initialGlobals: { theme: "light" },
  parameters: {
    layout: "fullscreen",
    controls: { matchers: { color: /(background|color)$/i, date: /Date$/i } },
  },
};

export default preview;
