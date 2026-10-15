"use client";

import Link from "next/link";
import type { Approval } from "@/lib/types";
import { StatusBadge } from "@/components/ui/status-badge";

export function ApprovalTable({ approvals }: { approvals: Approval[] }) {
  return <section className="content-section"><div className="table-scroll"><table>
    <thead><tr><th>Approval ID</th><th>Related decision</th><th>Requested</th><th>State</th></tr></thead>
    <tbody>{approvals.map((approval) => <tr key={approval.approval_id}>
      <td><Link href={`/approvals/${encodeURIComponent(approval.approval_id)}`}>{approval.approval_id}</Link></td>
      <td>{approval.decision_id}</td><td>{new Date(approval.requested_at).toLocaleString()}</td>
      <td><StatusBadge status={approval.status} /></td>
    </tr>)}</tbody>
  </table></div></section>;
}
