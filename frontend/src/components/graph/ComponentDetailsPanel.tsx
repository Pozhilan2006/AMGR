import { EmptyState } from "@/components/ui/empty-state";

export function ComponentDetailsPanel({
  component,
}: {
  component: object | null;
}) {
  if (!component) {
    return (
      <EmptyState
        description="Select a component to inspect its available metadata, timestamps, and relationship context."
        state="integration"
        title="No component selected"
      />
    );
  }

  const rows = Object.entries(component as Record<string, unknown>).filter(([, value]) => value !== undefined && value !== null && value !== "");

  return (
    <section className="content-section" aria-label="Component details">
      <div className="section-heading" style={{ marginBottom: 8 }}>
        <div>
          <p className="eyebrow">Component details</p>
          <h2>Selected node</h2>
        </div>
      </div>
      <dl className="integration-list">
        {rows.map(([key, value]) => (
          <div className="integration-row" key={key}>
            <dt>
              <span className="integration-name">{key}</span>
            </dt>
            <dd>
              {typeof value === "object" ? JSON.stringify(value) : String(value)}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
