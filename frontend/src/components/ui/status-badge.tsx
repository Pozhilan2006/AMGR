const knownStatuses: Record<string, string> = {
  allow: "status-badge-allow",
  approved: "status-badge-allow",
  block: "status-badge-block",
  deny: "status-badge-block",
  rejected: "status-badge-block",
  require_approval: "status-badge-review",
  review: "status-badge-review",
  pending: "status-badge-pending",
  unavailable: "status-badge-neutral",
  observed: "status-badge-neutral",
};

export function StatusBadge({ status }: { status: string }) {
  const className = knownStatuses[status.toLowerCase()] ?? "status-badge-neutral";

  return <span className={`status-badge ${className}`}>{status.replaceAll("_", " ")}</span>;
}
