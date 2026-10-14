const stateCopy = {
  "no-records": {
    label: "No matching records",
    title: "Nothing matched this query",
  },
  "not-collected": {
    label: "No data collected",
    title: "Runtime data has not been collected",
  },
  integration: {
    label: "Backend integration pending",
    title: "This data is not available yet",
  },
} as const;

export function EmptyState({
  state,
  description,
  title,
}: {
  state: keyof typeof stateCopy;
  description: string;
  title?: string;
}) {
  const copy = stateCopy[state];

  return (
    <section className="empty-state" aria-label={copy.label}>
      <span className="empty-state-mark" aria-hidden="true">—</span>
      <div>
        <p className="eyebrow">{copy.label}</p>
        <h2>{title ?? copy.title}</h2>
        <p>{description}</p>
      </div>
    </section>
  );
}
