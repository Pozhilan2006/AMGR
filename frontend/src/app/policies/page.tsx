"use client";

import { usePolicies } from "@/hooks/usePolicies";
import { PageHeader } from "@/components/layout/page-header";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { EmptyState } from "@/components/ui/empty-state";
import Link from "next/link";

export default function PoliciesPage() {
  const { policies, loading, error, unavailable, refresh } = usePolicies();
  return <div className="page-content">
    <PageHeader title="Policies" description="View backend policy records and their reported configuration." actions={<button className="primary-button" type="button" disabled title="Policy creation is not exposed by the backend">Create policy</button>} />
    {loading ? <LoadingState message="Loading policies…" /> : unavailable ? <EmptyState state="integration" description="The policy listing route currently returns 501 Not Implemented. Member 3 must implement policy storage and GET /policies before records or lifecycle controls can be shown." /> : error ? <ErrorState message={error} onRetry={() => void refresh()} /> : !policies.length ? <EmptyState state="no-records" description="The backend returned no policy records." /> :
      <section className="content-section"><div className="table-scroll"><table><thead><tr><th>Name</th><th>Status</th><th>Effect</th><th>Description</th><th>Policy ID</th></tr></thead><tbody>{policies.map((policy) => <tr key={policy.policy_id}><td><Link href={`/policies/${encodeURIComponent(policy.policy_id)}`}>{policy.name}</Link></td><td>{policy.enabled ? "Enabled" : "Disabled"}</td><td>{policy.effect}</td><td>{policy.description || "—"}</td><td>{policy.policy_id}</td></tr>)}</tbody></table></div></section>}
  </div>;
}
