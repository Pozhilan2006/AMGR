# Policy management and decision history frontend

## Implemented views

- `/policies` loads the policy list through the centralized API client and renders returned backend records. Its create action is visibly disabled because no write contract exists.
- `/policies/[id]` is prepared to show a backend policy detail, details metadata, and version-history state. Detail loading remains unavailable until the backend adds its route.
- `/decisions` loads through `useDecisions` and displays only backend decision records. The table/detail view uses the existing `AuthorizationDecision` schema fields (`decision_id`, `request_id`, `outcome`, `reason`, `policy_ids`, `evaluated_at`).
- Shared loading, error, empty, unauthorized, and unavailable messages are used. 401/403 responses retain the centralized API client's authentication and authorization messages.

## Backend contracts currently present

The API router mounts under the configured API v1 prefix (currently `/api/v1`).

| Method and path | Current contract/status | Frontend behavior |
| --- | --- | --- |
| `GET /api/v1/policies` | Route exists; always responds 501 with `Policy lifecycle is not implemented.` No response model or filters. | Requests through `getPolicies`; shows explicit integration-pending state on 501. |
| `POST /api/v1/authorization/decisions` | Accepts `AuthorizationRequest` (`request_id`, `flow_id`, optional `correlation_id`, `subject`, `action`, `resource`, `data_labels`, `metadata`); always responds 501. | Not called for history; explicitly not treated as a history endpoint. |

The existing response models in `backend/app/schemas/policy.py` and `backend/app/schemas/authorization.py` describe `Policy` and `AuthorizationDecision`, but there is no implemented persistence/read response contract. No list pagination, filters, write schema, lifecycle operation, version metadata, or history response is provided.

## Filters, lifecycle, and version history

No policy or decision filters or pagination are supported by the current API, so the frontend does not add client-side filtering or pagination. Create/edit/enable/disable/retire actions are unavailable because the backend exposes no mutation routes. Policy version history is explicitly shown as pending because no version-history route or snapshot schema exists.

## Navigation and integration gaps

Policy names link to `/policies/{policy_id}`. Decision rows expose only fields from the existing decision schema; the backend schema does not include source, destination, action, flow, or event references, so no graph, lineage, or decision-to-policy-version link can be generated honestly. Member 3 needs to provide policy list/detail/write/lifecycle/version-history routes and a scoped, paginated decision-history route with its supported filters and response schema. Graph and lineage links can be added when decision records return relevant identifiers and the corresponding graph APIs support navigation.
