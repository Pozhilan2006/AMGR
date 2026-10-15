"use client";

import type { AuthorizationDecision } from "@/lib/types";

export function DecisionDetails({ decision }: { decision: AuthorizationDecision }) {
  return <section className="content-section" aria-label={`Decision ${decision.decision_id}`}>
    <h2>Decision details</h2><dl>
      <dt>Decision ID</dt><dd>{decision.decision_id}</dd><dt>Request ID</dt><dd>{decision.request_id}</dd>
      <dt>Timestamp</dt><dd>{decision.evaluated_at}</dd><dt>Outcome</dt><dd>{decision.outcome}</dd>
      <dt>Backend reason</dt><dd>{decision.reason}</dd><dt>Policy IDs</dt><dd>{decision.policy_ids.length ? decision.policy_ids.join(", ") : "Not provided"}</dd>
    </dl></section>;
}
