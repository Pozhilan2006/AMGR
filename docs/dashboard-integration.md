# Dashboard integration status

This inventory reflects the backend routes and services currently present in
the repository. The API prefix defaults to `/api/v1`.

## Endpoint inventory

| Method and path | Implementation | Frontend integration |
| --- | --- | --- |
| `GET /api/v1/health` | Implemented; returns `{ "status": "ok" }`. | Overview connectivity check. It does not attest to data-service health. |
| `GET /api/v1/graph` | Route returns HTTP 501. | Graph page shows the unavailable state; no aggregate metric uses this route. |
| `POST /api/v1/flow-events` | Persists scoped events after migration; requires authenticated identity scope. | Instrumenter ingestion, not called by dashboard pages. |
| `GET /api/v1/lineage/events/{event_id}` | Implemented; bounded traversal over explicit parent-event references. | Available for event context; no event detail page yet. |
| `GET /api/v1/lineage/flows/{flow_id}` | Implemented; offset-paginated events in timestamp/event-ID order. | Available API; does not infer causal links from flow IDs. |
| `GET /api/v1/lineage/components/{component_id}` | Implemented; one-hop observed interactions with direction filtering. | Graph page queries after node selection; graph snapshot remains unavailable. |
| `GET /api/v1/policies` | Route returns HTTP 501. | Policy list is explicitly unavailable. Policy detail and version APIs are missing. |
| `POST /api/v1/authorization/decisions` | Route returns HTTP 501. | Not a history query. There is no read-only decision endpoint. |
| `GET /api/v1/approvals` | Route returns HTTP 501. | Approval list is explicitly unavailable. Detail and reviewer operations are missing. |
| `POST /api/v1/operator/chat` | Handler and Pydantic contract exist, but request identity must be provided by middleware; no such middleware is registered in the current app. Provider configuration is also required. | Operator chat sends the supported request schema and handles typed answer, clarification, proposal, and error responses. It is not fully operable with the current app wiring. |

The operator request accepts `message`, optional `conversation_id` (UUID), and
optional `scope` (`application_id`, `environment`). The response includes
`conversation_id`, `assistant_message`, `response_type`, optional evidence
references (`graph_event`, `policy`, `decision`), and optional proposed
actions. The backend conversation manager is stateless; no history retrieval
or streaming route exists. When an identity has multiple authorized scopes,
the backend requires an explicit permitted scope, but the frontend has no
authorized-scope discovery API or selection workflow.

## Dashboard data and navigation

Only the health response is currently a successful general dashboard read.
There are no aggregate counts, recent records, or complete datasets from which
the frontend could safely calculate metrics. The overview intentionally shows
service status rather than estimates.

Supported frontend destinations:

- Policy IDs link to `/policies/[id]`, whose backend detail request remains
  unavailable.
- Approval IDs link to `/approvals/[id]`, whose backend detail request remains
  unavailable.
- Decision references have no detail page/API and are not linked.
- Graph-event references have no event-detail route or query handler and are
  not linked.
- Decision policy identifiers may link to policy detail; no policy-version
  identifier is currently returned.

The graph snapshot, neighborhood, generic event history, decision history,
policy listing/detail/lifecycle, approval queue/detail/actions, and operator
impact or approval data remain unavailable or partial as documented above.
Lineage does have bounded traversal and offset pagination as described in
`lineage-and-provenance.md`; other unavailable interfaces have no pagination
or filtering contract.

## Browser configuration and deployment gaps

The frontend reads `NEXT_PUBLIC_AMGR_API_URL` (preferred) or the compatibility
fallback `NEXT_PUBLIC_API_BASE_URL`. The local example is
`http://localhost:8000/api/v1`; it must be replaced by the deployed API base
URL or same-origin proxy before a production build. Since Next.js embeds public
environment values at build time, configure them when building the frontend.

The current backend app does not register operator identity middleware or CORS
middleware. No browser authentication contract is established in this
repository. Cross-origin browser use and authenticated operator access require
backend/proxy configuration outside this frontend integration. The existing
frontend Dockerfile starts the development server; it is not a production
deployment artifact.
