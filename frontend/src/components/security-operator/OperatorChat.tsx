"use client";

import { ErrorState } from "@/components/ui/error-state";
import { ChatMessage } from "@/components/security-operator/ChatMessage";
import { useOperatorChat } from "@/hooks/useOperatorChat";

export function OperatorChat() {
  const {
    messages,
    draft,
    setDraft,
    conversationId,
    error,
    isUnavailable,
    isSubmitting,
    retry,
    starterPrompts,
    submit,
  } = useOperatorChat();

  return (
    <section className="operator-shell" aria-label="AI Security Operator chat panel">
      <div className="operator-topbar">
        <div>
          <p className="eyebrow">Operator session</p>
          <h2>Security investigation</h2>
        </div>
        {conversationId ? (
          <span className="conversation-pill">Conversation {conversationId.slice(0, 8)}</span>
        ) : (
          <span className="conversation-pill muted">Current session only</span>
        )}
      </div>

      <div className="operator-contract-note">
        The current response contract has no impact-analysis or approval fields. This chat will not infer
        impact, approval state, or policy activation from assistant text.
      </div>

      {error ? (
        <ErrorState message={error} onRetry={retry} />
      ) : null}

      {isUnavailable ? (
        <div className="state-panel" role="status">
          <span className="state-indicator" aria-hidden="true">i</span>
          <p>
            Backend support is unavailable for this operator endpoint or the API is not configured.
          </p>
        </div>
      ) : null}

      {messages.length === 0 ? (
        <div className="operator-empty-state">
          <div className="operator-empty-header">
            <span className="empty-state-mark" aria-hidden="true">AI</span>
            <div>
              <p className="eyebrow">Investigation ready</p>
              <h3>Ask about decisions, evidence, or policy changes</h3>
            </div>
          </div>
          <div className="starter-prompt-grid" aria-label="Suggested prompts">
            {starterPrompts.map((prompt) => (
              <button
                key={prompt}
                className="prompt-button"
                onClick={() => void submit(prompt)}
                type="button"
              >
                {prompt}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="chat-thread" aria-live="polite">
          {messages.map((message) => (
            <ChatMessage key={message.id} message={message} />
          ))}
        </div>
      )}

      <form
        className="operator-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <label className="sr-only" htmlFor="operator-message-input">
          Message the AI Security Operator
        </label>
        <textarea
          id="operator-message-input"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask the operator to explain a blocked decision, summarize evidence, or draft a policy proposal…"
          rows={4}
        />
        <div className="composer-actions">
          <p className="composer-note">
            Conversation history is limited to the current backend session identifier. No server-side history fetch is exposed.
          </p>
          <button className="primary-button" disabled={!draft.trim() || isSubmitting} type="submit">
            {isSubmitting ? "Sending…" : "Send"}
          </button>
        </div>
      </form>
    </section>
  );
}
