import { EmptyState } from "@/components/ui/empty-state";

export function LineagePanel({
  unavailable = false,
  lineage,
}: {
  unavailable?: boolean;
  lineage?: Array<Record<string, unknown>>;
}) {
  if (unavailable) {
    return (
      <EmptyState
        description="The backend does not expose upstream or downstream lineage records yet. This view remains unavailable until Member 2 implements the lineage service contract."
        state="integration"
        title="Lineage data unavailable"
      />
    );
  }

  if (!lineage || lineage.length === 0) {
    return (
      <EmptyState
        description="No matching lineage records were returned for the current component. The current graph may be incomplete or the backend may not yet expose this path."
        state="not-collected"
        title="No lineage records returned"
      />
    );
  }

  return (
    <section className="content-section" aria-label="Lineage details">
      <div className="section-heading" style={{ marginBottom: 8 }}>
        <div>
          <p className="eyebrow">Lineage</p>
          <h2>Upstream and downstream references</h2>
        </div>
      </div>
      <ul style={{ display: "grid", gap: 8, listStyle: "none", margin: 0, padding: 0 }}>
        {lineage.map((entry, index) => (
          <li key={index} className="state-panel">
            <pre style={{ margin: 0, whiteSpace: "pre-wrap", width: "100%" }}>
              {JSON.stringify(entry, null, 2)}
            </pre>
          </li>
        ))}
      </ul>
    </section>
  );
}
