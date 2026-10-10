export function FeaturePlaceholder({ title, description }: { title: string; description: string }) {
  return (
    <div className="page-content">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Workspace / Placeholder</p>
          <h1>{title}</h1>
        </div>
        <span className="status-label">Not implemented</span>
      </div>
      <div className="placeholder-rule" aria-hidden="true" />
      <section className="empty-state" aria-label={`${title} implementation status`}>
        <div className="empty-state-mark" aria-hidden="true">--</div>
        <div>
          <p className="eyebrow">Feature placeholder</p>
          <h2>{title} is not connected</h2>
          <p>{description}</p>
        </div>
      </section>
    </div>
  );
}