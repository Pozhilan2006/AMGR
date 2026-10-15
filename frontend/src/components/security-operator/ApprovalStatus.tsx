import Link from "next/link";

export function ApprovalStatus({
  approvalId,
  status,
}: {
  approvalId?: string | null;
  status?: string | null;
}) {
  const resolvedStatus = status?.trim();
  const resolvedId = approvalId?.trim();

  if (!resolvedId && !resolvedStatus) {
    return (
      <div className="info-card">
        <div className="card-header">
          <p className="eyebrow">Approval status</p>
          <span className="status-badge status-badge-neutral">Unavailable</span>
        </div>
        <p className="card-summary">No approval record was included in this backend response.</p>
      </div>
    );
  }

  return (
    <div className="info-card">
      <div className="card-header">
        <p className="eyebrow">Approval status</p>
        {resolvedStatus ? <span className="status-badge status-badge-review">{resolvedStatus}</span> : null}
      </div>
      <div className="approval-row">
        {resolvedId ? (
          <>
            <span className="muted-text">Approval ID</span>
            <Link className="inline-link" href={`/approvals/${encodeURIComponent(resolvedId)}`}>
              {resolvedId}
            </Link>
          </>
        ) : null}
      </div>
    </div>
  );
}
