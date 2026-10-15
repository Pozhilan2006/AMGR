import Link from "next/link";
import type { Approval } from "@/lib/types";
import { StatusBadge } from "@/components/ui/status-badge";

export function ApprovalDetails({ approval }: { approval: Approval }) {
  return <section className="content-section" aria-label="Approval request details">
    <h2>Request record</h2><dl>
      <dt>Approval ID</dt><dd>{approval.approval_id}</dd>
      <dt>State</dt><dd><StatusBadge status={approval.status} /></dd>
      <dt>Requested at</dt><dd>{new Date(approval.requested_at).toLocaleString()}</dd>
      <dt>Related decision</dt><dd>{approval.decision_id}</dd>
    </dl>
    <p>Requester, request type, operation context, expiration, reviewer history, and activation state are not included in the backend approval schema.</p>
    <p>Related policy context is not available from this approval record. <Link href="/policies">Open policy management</Link></p>
  </section>;
}
