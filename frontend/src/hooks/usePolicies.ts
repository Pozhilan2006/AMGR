"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, getPolicies } from "@/lib/api";
import type { Policy } from "@/lib/types";

export function usePolicies() {
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const refresh = useCallback(async () => {
    setLoading(true); setError(null); setUnavailable(false);
    try { setPolicies(await getPolicies()); }
    catch (cause) {
      setPolicies([]);
      setUnavailable(cause instanceof ApiError && (cause.kind === "unavailable" || cause.status === 501));
      setError(cause instanceof ApiError ? cause.userMessage : "Could not load policies.");
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  return { policies, loading, error, unavailable, refresh };
}
