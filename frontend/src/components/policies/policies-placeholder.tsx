export function PoliciesPlaceholder() {
  return (
    <section className="empty-state" aria-label="Policies placeholder">
      <div className="empty-state-mark" aria-hidden="true">PO</div>
      <div>
        <p className="eyebrow">Planned policy module</p>
        <h2>Policy lifecycle</h2>
        <p>Policy creation, precedence, and deterministic evaluation are not implemented in this starter.</p>
      </div>
    </section>
  );
}
