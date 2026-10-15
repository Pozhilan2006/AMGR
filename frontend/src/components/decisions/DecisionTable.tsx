"use client";

import Link from "next/link";

import type { AuthorizationDecision } from "@/lib/types";
import { DecisionDetails } from "@/components/decisions/DecisionDetails";
import { useState } from "react";

export function DecisionTable({ decisions }: { decisions: AuthorizationDecision[] }) {
  const [selected, setSelected] = useState<string | null>(null);
  if (!decisions.length) return <p>No authorization decisions were returned.</p>;
  return <div className="table-scroll"><table><thead><tr><th>Decision</th><th>Time</th><th>Outcome</th><th>Reason</th><th>Policies</th></tr></thead>
    <tbody>{decisions.map((decision) => <tr key={decision.decision_id}><td><button className="secondary-button" type="button" aria-expanded={selected === decision.decision_id} onClick={() => setSelected(selected === decision.decision_id ? null : decision.decision_id)}>{decision.decision_id}</button></td><td>{new Date(decision.evaluated_at).toLocaleString()}</td><td>{decision.outcome}</td><td>{decision.reason}</td><td>{decision.policy_ids.length ? decision.policy_ids.map((policyId, index) => <span key={`${policyId}-${index}`}>{index > 0 ? ", " : ""}<Link href={`/policies/${encodeURIComponent(policyId)}`}>{policyId}</Link></span>) : "—"}</td></tr>)}</tbody></table>
    {decisions.filter((decision) => selected === decision.decision_id).map((decision) => <DecisionDetails key={decision.decision_id} decision={decision} />)}</div>;
}
