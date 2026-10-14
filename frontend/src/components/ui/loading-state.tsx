export function LoadingState({ message = "Loading…" }: { message?: string }) {
  return (
    <div className="state-panel" aria-busy="true" role="status">
      <span className="state-indicator state-indicator-loading" aria-hidden="true" />
      <p>{message}</p>
    </div>
  );
}
