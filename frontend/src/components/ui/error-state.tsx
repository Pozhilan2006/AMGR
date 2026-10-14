export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="state-panel state-panel-error" role="alert">
      <span className="state-indicator state-indicator-error" aria-hidden="true">!</span>
      <p>{message}</p>
      {onRetry ? (
        <button className="secondary-button" onClick={onRetry} type="button">
          Try again
        </button>
      ) : null}
    </div>
  );
}
