"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, getHealth } from "@/lib/api";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { StatusBadge } from "@/components/ui/status-badge";

type HealthState =
  | { kind: "loading" }
  | { kind: "ready"; status: string }
  | { kind: "error"; message: string };

function getSafeErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.userMessage;
  }
  return "The API health status could not be loaded.";
}

export function BackendHealth() {
  const [state, setState] = useState<HealthState>({ kind: "loading" });
  const [refreshCount, setRefreshCount] = useState(0);

  const loadHealth = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const result = await getHealth();
      setState({ kind: "ready", status: result.status });
    } catch (error) {
      setState({ kind: "error", message: getSafeErrorMessage(error) });
    }
  }, []);

  useEffect(() => {
    void loadHealth();
  }, [loadHealth, refreshCount]);

  return (
    <section className="content-section" aria-labelledby="backend-health-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">API connection</p>
          <h2 id="backend-health-heading">Backend health</h2>
        </div>
      </div>
      {state.kind === "loading" ? <LoadingState message="Checking the configured AMGR API…" /> : null}
      {state.kind === "error" ? (
        <ErrorState
          message={state.message}
          onRetry={() => setRefreshCount((count) => count + 1)}
        />
      ) : null}
      {state.kind === "ready" ? (
        <div className="health-panel">
          <StatusBadge status={state.status} />
          <p>The API health endpoint responded. This does not indicate that data services are available.</p>
        </div>
      ) : null}
    </section>
  );
}
