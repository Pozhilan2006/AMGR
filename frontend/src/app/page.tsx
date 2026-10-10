export default function OverviewPage() {
  return (
    <div className="page-content">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Workspace / Overview</p>
          <h1>Runtime overview</h1>
        </div>
        <span className="status-label"><span className="status-dot" /> Starter workspace</span>
      </div>

      <section className="empty-state" aria-labelledby="overview-empty-title">
        <div className="empty-state-mark" aria-hidden="true">AM</div>
        <div>
          <p className="eyebrow">No connected runtime</p>
          <h2 id="overview-empty-title">Overview placeholder</h2>
          <p>Live event ingestion and runtime metrics are not implemented yet.</p>
        </div>
      </section>

      <div className="surface-note">
        <span className="note-index">01</span>
        <p>API health is available at <code>/api/v1/health</code>. Data and enforcement surfaces remain unimplemented.</p>
      </div>
    </div>
  );
}