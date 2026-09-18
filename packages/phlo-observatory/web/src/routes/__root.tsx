/**
 * Root route: installs the query client, theme and environment providers,
 * mounts the app shell, and declares the stylesheet for the document head.
 */
import { HeadContent, Scripts, createRootRoute } from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import appCss from "../styles.css?url";
import { AppShell } from "@/components/app/app-shell";
import { EnvironmentProvider } from "@/components/app/environment-provider";
import { ThemeProvider } from "@/components/app/theme-provider";
import { TooltipProvider } from "@/components/ui/tooltip";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Matches the footer's 15s auto-refresh promise.
      staleTime: 15_000,
      retry: 1,
      refetchOnWindowFocus: true,
    },
  },
});

export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "Phlo Mission Control" },
    ],
    links: [{ rel: "stylesheet", href: appCss }],
  }),
  component: RootComponent,
});

function RootComponent() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <EnvironmentProvider>
          <TooltipProvider>
            <HeadContent />
            <AppShell />
            <Scripts />
          </TooltipProvider>
        </EnvironmentProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
