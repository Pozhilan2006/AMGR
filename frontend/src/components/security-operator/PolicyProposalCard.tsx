import type { OperatorResponseType, ProposedAction } from "@/lib/types";

export function PolicyProposalCard({
  actions,
  summary,
  responseType,
}: {
  actions: ProposedAction[];
  summary?: string | null;
  responseType: OperatorResponseType;
}) {
  const label = responseType === "proposal" ? "Draft proposal" : "Structured proposal";

  return (
    <div className="info-card proposal-card">
      <div className="card-header">
        <p className="eyebrow">{label}</p>
        <span className="status-badge status-badge-review">Proposal</span>
      </div>

      {summary ? <p className="card-summary">{summary}</p> : null}

      <ul className="proposal-list">
        {actions.map((action) => (
          <li key={`${action.title}-${action.description}`}>
            <h4>{action.title}</h4>
            <p>{action.description}</p>
            <span className="proposal-review">
              {action.review_required ? "Backend review required" : "Review not required"}
            </span>
          </li>
        ))}
      </ul>

      <p className="muted-text">
        This card reflects a backend-provided draft or proposal. The frontend does not validate,
        activate, or approve policy changes.
      </p>
    </div>
  );
}
