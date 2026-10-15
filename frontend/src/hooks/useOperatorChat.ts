"use client";

import { useCallback, useState } from "react";

import { ApiError, sendOperatorMessage } from "@/lib/api";
import type { OperatorChatRequest, OperatorChatResponse, OperatorReference, ProposedAction } from "@/lib/types";

export type ChatMessageEntry = {
  id: string;
  role: "user" | "assistant";
  content: string;
  responseType?: OperatorChatResponse["response_type"] | null;
  references?: OperatorReference[] | null;
  proposedActions?: ProposedAction[] | null;
};

export const starterPrompts = [
  "Show the downstream components connected to this agent.",
  "Explain this blocked decision.",
  "Draft a policy restricting this agent's external access.",
];

export function useOperatorChat() {
  const [messages, setMessages] = useState<ChatMessageEntry[]>([]);
  const [draft, setDraft] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isUnavailable, setIsUnavailable] = useState(false);
  const [failedMessage, setFailedMessage] = useState<string | null>(null);

  const submit = useCallback(
    async (messageText?: string, retry = false) => {
      const trimmed = (retry ? failedMessage : messageText ?? draft)?.trim() ?? "";
      if (!trimmed || isSubmitting) {
        return;
      }

      if (!retry) {
        const userMessage: ChatMessageEntry = {
          id: `user-${crypto.randomUUID()}`,
          role: "user",
          content: trimmed,
        };
        setMessages((current) => [...current, userMessage]);
        setDraft("");
        setFailedMessage(null);
      }
      setError(null);
      setIsUnavailable(false);
      setIsSubmitting(true);

      const request: OperatorChatRequest = {
        message: trimmed,
        ...(conversationId ? { conversation_id: conversationId } : {}),
      };

      try {
        const response = await sendOperatorMessage(request);
        setConversationId(response.conversation_id);

        const assistantMessage: ChatMessageEntry = {
          id: `assistant-${response.conversation_id}-${Date.now()}`,
          role: "assistant",
          content: response.assistant_message,
          responseType: response.response_type,
          references: response.references ?? null,
          proposedActions: response.proposed_actions ?? null,
        };

        setMessages((current) => [...current, assistantMessage]);

        if (response.response_type === "error") {
          setFailedMessage(trimmed);
          setError(response.assistant_message);
        } else {
          setFailedMessage(null);
        }
      } catch (cause) {
        const message = cause instanceof ApiError ? cause.userMessage : "The operator request failed.";
        setIsUnavailable(cause instanceof ApiError && (cause.kind === "unavailable" || cause.status === 501));
        setFailedMessage(trimmed);
        setError(message);
      } finally {
        setIsSubmitting(false);
      }
    },
    [conversationId, draft, failedMessage, isSubmitting],
  );

  const retry = useCallback(() => {
    void submit(undefined, true);
  }, [submit]);

  return {
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
  };
}
