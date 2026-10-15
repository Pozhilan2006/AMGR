"use client";

import { useApprovals } from "@/hooks/useApprovals";
import { PageHeader } from "@/components/layout/page-header";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";
import { ApprovalTable } from "@/components/approvals/ApprovalTable";

export default function ApprovalsPage() {
  const { approvals, loading, error, unavailable, refresh } = useApprovals();
  return <div className="page-content"><PageHeader title="Approvals" description="Review approval request records returned by the AMGR backend." />
    {loading ? <LoadingState message="Loading approvals…" /> : unavailable ? <EmptyState state="integration" description="GET /api/v1/approvals currently returns 501 Not Implemented. Member 3 must implement approval persistence and the queue contract before requests can be displayed." /> : error ? <ErrorState message={error} onRetry={() => void refresh()} /> : !approvals.length ? <EmptyState state="no-records" description="The backend returned no approval requests." /> : <ApprovalTable approvals={approvals} />}
  </div>;
}
