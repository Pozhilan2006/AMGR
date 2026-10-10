export function ApprovalsPlaceholder() {
  return (
    <section className="empty-state" aria-label="Approvals placeholder">
      <div className="empty-state-mark" aria-hidden="true">AP</div>
      <div>
        <p className="eyebrow">Planned approval module</p>
        <h2>Approval workflow</h2>
        <p>Approval routing and decision state tracking will be implemented once the policy and enforcement service is ready.</p>
      </div>
    </section>
  );
}
