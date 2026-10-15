"use client";

import { useDecisions } from "@/hooks/useDecisions";
import { PageHeader } from "@/components/layout/page-header";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";
import { DecisionTable } from "@/components/decisions/DecisionTable";

export default function DecisionsPage() {
  const { decisions, loading, error, unavailable, refresh } = useDecisions();
  return <div className="page-content"><PageHeader title="Authorization decisions" description="Read-only history of backend authorization outcomes." />
    {loading ? <LoadingState message="Loading decision history…" /> : unavailable ? <EmptyState state="integration" description="There is no read-only decision-history endpoint. POST /authorization/decisions is an authorization request route and currently returns 501; Member 3 must expose a scoped history route before records can appear." /> : error ? <ErrorState message={error} onRetry={() => void refresh()} /> : !decisions.length ? <EmptyState state="no-records" description="The backend returned no decision records." /> : <DecisionTable decisions={decisions} />}
  </div>;
}
