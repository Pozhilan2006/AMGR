# Security operator frontend

## Implemented views and components

- `/operator` renders a session-based operator chat panel using the centralized client in `frontend/src/lib/api.ts`.
- `frontend/src/components/security-operator/OperatorChat.tsx` manages the live chat composer, empty state, retry state, and starter prompts.
- `frontend/src/components/security-operator/ChatMessage.tsx` separates user and assistant messages and renders response metadata alongside the assistant text.
- `frontend/src/components/security-operator/EvidenceReferences.tsx` displays `references` returned from the backend and links only to valid frontend routes when a record identifier is safe to navigate.
- `frontend/src/components/security-operator/PolicyProposalCard.tsx` renders structured backend proposals without inventing validation or activation results.
- `frontend/src/components/security-operator/ImpactSummary.tsx` is a reusable unavailable-state component; it is not mounted because the current response schema has no impact field.
- `frontend/src/components/security-operator/ApprovalStatus.tsx` is a reusable approval display; it is not mounted because the current response schema has no approval identifier or status.
- `frontend/src/hooks/useOperatorChat.ts` manages conversation state, starter prompts, and request submission.

## Actual backend endpoints used

The frontend calls the authenticated operator route defined by the backend:

- `POST /api/v1/operator/chat`

The route requires middleware-provided operator identity data and a provider configuration. No middleware that populates `request.state.operator_identity` is currently registered in `backend/app/main.py`, so the route returns 401 in the current application wiring. It responds with a JSON body matching the Pydantic schema in `backend/app/ai_operator/schemas.py`.

## Request and response schema supported

Request shape:

```json
{
  "message": "Explain this blocked decision.",
  "conversation_id": "uuid-or-null",
  "scope": {
    "application_id": "string",
    "environment": "string"
  }
}
```

Response shape:

```json
{
  "conversation_id": "uuid",
  "assistant_message": "text",
  "response_type": "answer | clarification | proposal | error",
  "references": [
    {
      "reference_type": "graph_event | policy | decision",
      "reference_id": "identifier"
    }
  ],
  "proposed_actions": [
    {
      "title": "string",
      "description": "string",
      "review_required": true
    }
  ]
}
```

The frontend intentionally does not assume any unsupported streaming contract or additional fields.

## Conversation identifier and history behavior

- The backend correlates a request with a `conversation_id` when provided.
- The backend is intentionally stateless with respect to stored message history; it resolves the ID but does not expose a history retrieval endpoint.
- The frontend keeps the current `conversation_id` in session state only and does not imply durable message history beyond the current chat session.

## Supported structured response types

The interface renders the response types actually shipped by the backend:

- `answer`: plain assistant response with optional references.
- `clarification`: backend requests missing context to proceed.
- `proposal`: structured draft or proposal, rendered via `PolicyProposalCard`.
- `error`: backend or provider failure; surfaced as an explicit error state.

No frontend-only LLM logic is added, and the interface does not parse arbitrary markdown as executable instructions.

## Evidence reference navigation

Evidence links are generated only when the backend provides a supported identifier and a meaningful frontend target exists:

- `policy` -> `/policies/[id]`
- `decision` and `graph_event` references are shown without links because no corresponding record-detail route or identifier query handler exists.

When a backend record is not represented by a valid route, the UI shows an unresolved state instead of inventing a detail page.

## Policy proposal rendering and validation states

The policy proposal card displays:

- the assistant text summary,
- proposal actions and review requirements,
- a label that marks the content as a draft/proposal, not an active policy,
- a note that validation and activation remain authoritative backend responsibilities.

The frontend does not perform its own policy validation or activation.

## Impact-analysis availability

The current backend response contract does not contain a typed impact-analysis payload. The operator page displays a contract limitation notice, and the reusable impact component is not mounted. The frontend does not estimate impacts or fabricate metrics.

## Approval integration and activation-state handling

The current operator response contract has no approval identifier or status, so the operator chat does not render approval state. The reusable approval component can display such data once the backend contract supplies it. Activation or approval actions are never performed from the chat interface itself.

## Remaining dependencies and backend gaps

The operator frontend submits the actual backend request schema, but the route is not currently operable through the application because identity middleware is not registered. The following remain backend dependencies:

1. Authenticated identity and scope provisioning in middleware.
2. Provider configuration and provider availability for the AI operator.
3. Durable history or retrieval APIs beyond current correlation IDs.
4. Full evidence retrieval quality and supported reference types.
5. Policy proposal validation and approval lifecycle integration.
6. Any impact-analysis payload or richer proposal metadata beyond the current schema.

## Completion summary

Modified files include:

- `frontend/src/app/operator/page.tsx`
- `frontend/src/components/security-operator/*`
- `frontend/src/hooks/useOperatorChat.ts`
- `frontend/src/lib/types.ts`
- `docs/security-operator-frontend.md`

The operator interface is wired to the route and contract, but end-to-end use waits on backend identity middleware, provider configuration, and any desired richer response fields. UI states report those gaps instead of inventing unsupported features.
