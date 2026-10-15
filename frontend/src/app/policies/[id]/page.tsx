"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { ApiError, getPolicy } from "@/lib/api";
import type { Policy } from "@/lib/types";
import { PageHeader } from "@/components/layout/page-header";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";
import { PolicyDetails } from "@/components/policies/PolicyDetails";
import { PolicyForm } from "@/components/policies/PolicyForm";
import { PolicyVersionHistory } from "@/components/policies/PolicyVersionHistory";

export default function PolicyPage() {
  const params = useParams<{ id: string }>();
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    let active = true;
    getPolicy(params.id).then((item) => { if (active) setPolicy(item); }).catch((cause) => {
      if (!active) return;
      setUnavailable(cause instanceof ApiError && (cause.kind === "unavailable" || cause.status === 501));
      setError(cause instanceof ApiError ? cause.userMessage : "Could not load this policy.");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [params.id]);
  return <div className="page-content"><PageHeader title="Policy details" description={`Policy ${params.id}`} />
    {loading ? <LoadingState message="Loading policy…" /> : unavailable ? <EmptyState state="integration" title="Policy details unavailable" description="Member 3 has not implemented GET /policies/{policy_id}. The listing endpoint also currently returns 501." /> : policy ? <><PolicyDetails policy={policy} /><PolicyVersionHistory policyId={policy.policy_id} /><PolicyForm policy={policy} /></> : error ? <ErrorState message={error} /> : null}
  </div>;
}
