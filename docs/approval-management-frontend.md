# Approval management frontend

## Implemented views

- `/approvals` calls the shared `getApprovals()` client function and renders returned records using `ApprovalTable` and the shared status badge. The queue presents only schema-backed fields: approval ID, related decision ID, request time, and state.
- `/approvals/[id]` is structured to display an approval detail, policy-change summary, and reviewer action area. The current backend has no detail route, so the page shows an integration-pending state and does not invent a record.
- `useApprovals` handles loading, empty, errors, HTTP 401/403 messages, and 501/unavailable states. The API client remains centralized in `frontend/src/lib/api.ts`.

## Backend contracts found

The API router mounts routes under `/api/v1`.

| Method and path | Current behavior | Frontend use |
| --- | --- | --- |
| `GET /api/v1/approvals` | Route exists but always returns 501 with `Approval processing is not implemented.` It has no response model. | Called by queue via `getApprovals()`, which shows the unavailable state on 501. |

`backend/app/schemas/approval.py` defines an `Approval` model with `approval_id` (UUID), `decision_id` (UUID), `status` (`pending`, `approved`, or `rejected`), and `requested_at` (datetime). This model is not currently connected to a working route or persistence service.

## Filters and workflow behavior

The current API provides no pagination or state/request-type filters. The schema has no request type, requester, expiration, operation context, policy proposal, reviewer comment, audit history, activation state, or impact/graph references. Accordingly, the frontend does not add unsupported filters or derive missing values.

There is no approval detail route, approve/reject endpoint, request body, reviewer permission contract, expiration behavior, or conflict response behavior. The action controls are disabled and explicitly identify the missing backend contract. No confirmation or submission flow is exposed until a valid operation exists. The frontend does not mark requests approved/rejected or infer that approval activates a policy.

## Policy and graph dependencies

The detail components distinguish absent proposal and context data; they do not calculate policy effects or impact. Existing policy navigation is available, but an approval record currently provides no policy identifier or version to link. Graph and lineage navigation require backend references that are not present in the approval schema.

## Backend dependencies remaining

Member 3 needs to implement an approval queue response contract and persistence, detail retrieval, supported filters and pagination, approve/reject operations and bodies, reviewer authorization/scope rules, expiration and conflict semantics, and any proposal, audit, impact, activation, or graph references intended for display. Approval state and activation state should remain separately represented if both are exposed.
