/**
 * Environment Provider component.
 */
import { createContext, useContext, useState } from "react";

import type { Environment } from "@/data/demo";

interface EnvironmentContextValue {
  environment: Environment;
  setEnvironment: (environment: Environment) => void;
}

const EnvironmentContext = createContext<EnvironmentContextValue | null>(null);

/**
 * Selected deployment environment. Shared because pages label their data with
 * it (footer, status chips) and every API call in stage 2 is environment-scoped.
 */
export function EnvironmentProvider({ children }: { children: React.ReactNode }) {
  const [environment, setEnvironment] = useState<Environment>("Production");
  return (
    <EnvironmentContext.Provider value={{ environment, setEnvironment }}>
      {children}
    </EnvironmentContext.Provider>
  );
}

export function useEnvironment() {
  const ctx = useContext(EnvironmentContext);
  if (!ctx) throw new Error("useEnvironment must be used within EnvironmentProvider");
  return ctx;
}
