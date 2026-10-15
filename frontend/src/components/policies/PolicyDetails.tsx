import Link from "next/link";
import type { Policy } from "@/lib/types";

export function PolicyDetails({ policy }: { policy: Policy }) {
  return <section className="content-section" aria-label="Policy details">
    <h2>{policy.name}</h2>
    <p>{policy.description || "No description provided."}</p>
    <dl><dt>Policy ID</dt><dd>{policy.policy_id}</dd><dt>Status</dt><dd>{policy.enabled ? "Enabled" : "Disabled"}</dd>
      <dt>Effect</dt><dd>{policy.effect}</dd><dt>Conditions</dt><dd><pre>{JSON.stringify(policy.conditions, null, 2)}</pre></dd></dl>
  </section>;
}

export function PolicyReference({ policyId }: { policyId: string }) {
  return <Link href={`/policies/${encodeURIComponent(policyId)}`}>{policyId}</Link>;
}
