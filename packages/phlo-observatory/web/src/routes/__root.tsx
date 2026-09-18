/**
 * Root route: installs theme and environment providers, mounts the app shell,
 * and declares the stylesheet and font links for the document head.
 */
import { HeadContent, Scripts, createRootRoute } from "@tanstack/react-router";

import { AppShell } from "@/components/app-shell";
import { EnvironmentProvider } from "@/components/environment-provider";
import { ThemeProvider } from "@/components/theme-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
import appCss from "../styles.css?url";

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
    <ThemeProvider>
      <EnvironmentProvider>
        <TooltipProvider>
          <HeadContent />
          <AppShell />
          <Scripts />
        </TooltipProvider>
      </EnvironmentProvider>
    </ThemeProvider>
  );
}
