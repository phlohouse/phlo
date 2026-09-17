/**
 * Light/dark theme state persisted to localStorage and documentElement dataset.
 */
import * as React from "react";

export type Theme = "light" | "dark";

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = React.useState<Theme>(() => {
    if (typeof window === "undefined") return "light";
    return (window.localStorage.getItem("phlo-theme") as Theme) || "light";
  });
  React.useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("phlo-theme", theme);
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "light" ? "dark" : "light"))];
}
