export function GraphPlaceholder() {
  return (
    <section className="empty-state" aria-label="Flow graph placeholder">
      <div className="empty-state-mark" aria-hidden="true">FG</div>
      <div>
        <p className="eyebrow">Planned graph module</p>
        <h2>Flow graph visualization</h2>
        <p>React Flow integration and runtime graph traversal are planned for a later milestone.</p>
      </div>
    </section>
  );
}
