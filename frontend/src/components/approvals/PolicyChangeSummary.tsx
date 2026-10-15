import type { Approval } from "@/lib/types";

export function PolicyChangeSummary({ approval }: { approval: Approval }) {
  return <section className="content-section" aria-label="Policy change summary">
    <h2>Policy change</h2>
    <p>No policy proposal is attached to approval {approval.approval_id} in the available backend schema. Proposed or active policy values cannot be shown.</p>
  </section>;
}
