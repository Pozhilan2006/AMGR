"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { ApiError, getApproval } from "@/lib/api";
import type { Approval } from "@/lib/types";
import { PageHeader } from "@/components/layout/page-header";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";
import { ApprovalDetails } from "@/components/approvals/ApprovalDetails";
import { ApprovalActions } from "@/components/approvals/ApprovalActions";
import { PolicyChangeSummary } from "@/components/approvals/PolicyChangeSummary";

export default function ApprovalPage() {
  const params = useParams<{ id: string }>();
  const [approval, setApproval] = useState<Approval | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    let active = true;
    getApproval(params.id).then((item) => { if (active) setApproval(item); }).catch((cause) => {
      if (!active) return;
      setUnavailable(cause instanceof ApiError && (cause.kind === "unavailable" || cause.status === 501));
      setError(cause instanceof ApiError ? cause.userMessage : "Could not load this approval.");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [params.id]);
  return <div className="page-content"><PageHeader title="Approval details" description={`Approval ${params.id}`} />
    {loading ? <LoadingState message="Loading approval…" /> : unavailable ? <EmptyState state="integration" title="Approval details unavailable" description="Member 3 has not implemented GET /api/v1/approvals/{approval_id}." /> : approval ? <><ApprovalDetails approval={approval} /><PolicyChangeSummary approval={approval} /><ApprovalActions approval={approval} /></> : error ? <ErrorState message={error} /> : null}
  </div>;
}
