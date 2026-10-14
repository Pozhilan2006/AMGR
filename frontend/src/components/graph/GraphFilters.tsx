import type { GraphFilterState } from "@/lib/types";

export function GraphFilters({
  available = false,
  filters,
  onChange,
  onReset,
}: {
  available?: boolean;
  filters: GraphFilterState;
  onChange: (next: GraphFilterState) => void;
  onReset: () => void;
}) {
  if (!available) {
    return (
      <section className="content-section" aria-label="Graph filters unavailable">
        <div className="state-panel">
          <span className="state-indicator" aria-hidden="true">i</span>
          <p>
            The backend does not expose supported graph filters yet. Server-side filtering,
            pagination limits, and traversal controls remain pending Member 2 integration.
          </p>
        </div>
      </section>
    );
  }

  const update = (key: keyof GraphFilterState, value: string) => {
    onChange({
      ...filters,
      [key]: value || undefined,
    });
  };

  return (
    <section className="content-section" aria-label="Graph filters">
      <div className="section-heading" style={{ marginBottom: 8 }}>
        <div>
          <p className="eyebrow">Filters</p>
          <h2>Active graph filters</h2>
        </div>
        <button className="secondary-button" onClick={onReset} type="button">
          Clear filters
        </button>
      </div>
      <div className="page-actions">
        <label>
          <span style={{ display: "block", marginBottom: 4 }}>Component type</span>
          <input
            onChange={(event) => update("componentType", event.target.value)}
            placeholder="agent, tool, api, db"
            type="text"
            value={filters.componentType ?? ""}
          />
        </label>
        <label>
          <span style={{ display: "block", marginBottom: 4 }}>Flow ID</span>
          <input
            onChange={(event) => update("flowId", event.target.value)}
            placeholder="flow identifier"
            type="text"
            value={filters.flowId ?? ""}
          />
        </label>
        <label>
          <span style={{ display: "block", marginBottom: 4 }}>Start date</span>
          <input
            onChange={(event) => update("start", event.target.value)}
            type="date"
            value={filters.start ?? ""}
          />
        </label>
        <label>
          <span style={{ display: "block", marginBottom: 4 }}>End date</span>
          <input
            onChange={(event) => update("end", event.target.value)}
            type="date"
            value={filters.end ?? ""}
          />
        </label>
      </div>
    </section>
  );
}
