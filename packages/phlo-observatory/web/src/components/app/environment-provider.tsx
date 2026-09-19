/**
 * Environment Provider component.
 */
import { createContext, useContext, useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import type { MissionContext, ReadEnvelope } from "@/api/types";
import { queries } from "@/api/mission-control";

interface EnvironmentContextValue {
  /** The deployment's one configured environment, or null while unknown. */
  environment: string | null;
  /** The full context envelope — identity, readiness, dependency status. */
  context: ReadEnvelope<MissionContext> | undefined;
  isLoading: boolean;
}

const EnvironmentContext = createContext<EnvironmentContextValue | null>(null);

/**
 * The deployment's configured environment, loaded from
 * `GET /api/observatory/mission/context`. One deployment serves exactly one
 * project/environment pair — there is nothing to switch between, so this is
 * a server fact, not client state. When the reported deployment identity
 * changes underneath a running session (restart against a different
 * project/environment), cached reads and pending previews are cleared so no
 * stale cross-project data can render.
 */
export function EnvironmentProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const context = useQuery(queries.context());
  const identity = context.data?.data
    ? `${context.data.data.project_id}|${context.data.data.environment_id}|${context.data.data.data_mode}`
    : null;
  const previousIdentity = useRef<string | null>(null);

  useEffect(() => {
    if (identity === null) return;
    if (previousIdentity.current !== null && previousIdentity.current !== identity) {
      queryClient.clear();
    }
    previousIdentity.current = identity;
  }, [identity, queryClient]);

  return (
    <EnvironmentContext.Provider
      value={{
        environment: context.data?.data?.environment_id ?? null,
        context: context.data,
        isLoading: context.isLoading,
      }}
    >
      {children}
    </EnvironmentContext.Provider>
  );
}

export function useEnvironment() {
  const ctx = useContext(EnvironmentContext);
  if (!ctx) throw new Error("useEnvironment must be used within EnvironmentProvider");
  return ctx;
}
