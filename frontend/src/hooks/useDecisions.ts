"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, getDecisionHistory } from "@/lib/api";
import type { AuthorizationDecision } from "@/lib/types";

export function useDecisions() {
  const [decisions, setDecisions] = useState<AuthorizationDecision[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const refresh = useCallback(async () => {
    setLoading(true); setError(null); setUnavailable(false);
    try { setDecisions(await getDecisionHistory()); }
    catch (cause) {
      setDecisions([]);
      setUnavailable(cause instanceof ApiError && (cause.kind === "unavailable" || cause.status === 501));
      setError(cause instanceof ApiError ? cause.userMessage : "Could not load decision history.");
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  return { decisions, loading, error, unavailable, refresh };
}
