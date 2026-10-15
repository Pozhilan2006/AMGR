import { EvidenceReferences } from "@/components/security-operator/EvidenceReferences";
import { PolicyProposalCard } from "@/components/security-operator/PolicyProposalCard";
import type { OperatorChatResponse, OperatorReference, ProposedAction } from "@/lib/types";

export type ChatMessageItem = {
  id: string;
  role: "user" | "assistant";
  content: string;
  responseType?: OperatorChatResponse["response_type"] | null;
  references?: OperatorReference[] | null;
  proposedActions?: ProposedAction[] | null;
};

export function ChatMessage({ message }: { message: ChatMessageItem }) {
  const isUser = message.role === "user";
  const responseLabel =
    message.responseType === "clarification"
      ? "Clarification requested"
      : message.responseType === "proposal"
        ? "Draft proposal"
        : message.responseType === "error"
          ? "Operator error"
          : "Assistant response";

  return (
    <article className={`chat-message chat-message-${isUser ? "user" : "assistant"}`}>
      <div className="message-bubble">
        <div className="message-meta">
          <strong>{isUser ? "You" : "AI Security Operator"}</strong>
          {!isUser ? <span>{responseLabel}</span> : null}
        </div>
        <div className="message-body">
          <p>{message.content}</p>
        </div>

        {!isUser && message.responseType === "clarification" ? (
          <div className="message-note">The backend requires additional context before it can continue.</div>
        ) : null}

        {!isUser && message.references && message.references.length > 0 ? (
          <EvidenceReferences references={message.references} />
        ) : null}

        {!isUser && message.proposedActions && message.proposedActions.length > 0 ? (
          <PolicyProposalCard actions={message.proposedActions} summary={message.content} responseType={message.responseType ?? "answer"} />
        ) : null}

      </div>
    </article>
  );
}
