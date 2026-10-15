"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, getApprovals } from "@/lib/api";
import type { Approval } from "@/lib/types";

export function useApprovals() {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const refresh = useCallback(async () => {
    setLoading(true); setError(null); setUnavailable(false);
    try { setApprovals(await getApprovals()); }
    catch (cause) {
      setApprovals([]);
      setUnavailable(cause instanceof ApiError && (cause.kind === "unavailable" || cause.status === 501));
      setError(cause instanceof ApiError ? cause.userMessage : "Could not load approvals.");
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  return { approvals, loading, error, unavailable, refresh };
}
