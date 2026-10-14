type GraphToolbarProps = {
  disabled?: boolean;
  onFit?: () => void;
  onRefresh?: () => void;
  onReset?: () => void;
  onZoomIn?: () => void;
  onZoomOut?: () => void;
};

export function GraphToolbar({
  disabled = false,
  onFit,
  onRefresh,
  onReset,
  onZoomIn,
  onZoomOut,
}: GraphToolbarProps) {
  return (
    <section className="content-section" aria-label="Graph tools">
      <div className="section-heading" style={{ marginBottom: 8 }}>
        <div>
          <p className="eyebrow">Graph tools</p>
          <h2>Viewport controls</h2>
        </div>
      </div>
      <div className="page-actions">
        <button className="secondary-button" disabled={disabled} onClick={onFit} type="button">
          Fit view
        </button>
        <button className="secondary-button" disabled={disabled} onClick={onZoomOut} type="button">
          Zoom out
        </button>
        <button className="secondary-button" disabled={disabled} onClick={onZoomIn} type="button">
          Zoom in
        </button>
        <button className="secondary-button" disabled={disabled} onClick={onReset} type="button">
          Reset selection
        </button>
        <button className="secondary-button" disabled={disabled} onClick={onRefresh} type="button">
          Refresh graph
        </button>
      </div>
    </section>
  );
}
