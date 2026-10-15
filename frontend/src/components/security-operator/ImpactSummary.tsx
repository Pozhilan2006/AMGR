export function ImpactSummary({
  summary,
  warnings,
}: {
  summary?: string | null;
  warnings?: string[] | null;
}) {
  const resolvedSummary = summary?.trim();
  const resolvedWarnings = warnings?.filter(Boolean) ?? [];

  return (
    <div className="info-card">
      <div className="card-header">
        <p className="eyebrow">Impact summary</p>
        <span className="status-badge status-badge-neutral">Unavailable</span>
      </div>

      <p className="card-summary">
        {resolvedSummary ||
          "No impact-analysis payload was returned by the backend for this response."}
      </p>

      {resolvedWarnings.length > 0 ? (
        <ul className="warning-list">
          {resolvedWarnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : (
        <p className="muted-text">
          The frontier operator response and evidence are shown as provided by the backend. No frontend-only estimate is added.
        </p>
      )}
    </div>
  );
}
