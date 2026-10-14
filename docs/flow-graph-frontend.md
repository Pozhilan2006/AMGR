# Flow graph frontend integration status

## Current implementation

The dashboard route `/graph` is an integration-status page. It does not render graph
nodes or relationships because the current backend does not provide graph records.
No mock or inferred graph data is used. The existing `/flow-graph` URL remains a
compatibility alias to the same status.

The frontend API helper targets the configured API base URL plus `GET /graph`.
With the default backend prefix, this is `GET /api/v1/graph`. The route exists in
`backend/app/api/routes/graph.py`, but always raises HTTP 501
(`Flow graph queries are not implemented.`).

## Declared schemas versus implemented API

`backend/app/schemas/graph.py` declares:

- `GraphResponse`: `nodes`, `edges`, and `generated_at`
- `GraphNode`: `id`, `component`, and optional `label`
- `GraphEdge`: `id`, `source`, `target`, and `action`
- `ComponentRef` (in `backend/app/schemas/flow_event.py`): `component_id`,
  `component_type`, and optional `name`

These are Pydantic models, not a working snapshot response contract yet: the
`GET /graph` route does not return or validate a `GraphResponse`. The frontend
TypeScript types reflect the declared field shape only and do not imply that the
endpoint is available.

## Other graph-related interfaces

| Capability | Repository interface | Current status |
| --- | --- | --- |
| Graph snapshot | `GET /api/v1/graph` | Route returns 501 |
| Component details | No read endpoint | Missing |
| Neighborhood query | No read endpoint | Missing |
| Event ingestion | `POST /api/v1/flow-events` | Route returns 501; ingestion only |
| Event history for a relationship | No read endpoint | Missing |
| Upstream/downstream lineage | `LineageService.resolve_lineage` | Placeholder raises `NotImplementedError`; no API route |
| Graph filters | No query parameters defined | Unsupported |
| Pagination/truncation metadata | No API response contract defined | Unsupported |

The project also declares `GraphService`, but its persistence/traversal method is
unimplemented. The available `FlowEvent` schema includes event, flow,
source/destination component, action, data-label, status, and timestamp fields;
it does not mean events are persisted or queryable.

## Member 2 dependencies

Before a live interactive graph can be implemented, Member 2 needs to provide and
document:

1. A working graph snapshot endpoint backed by persisted graph data, with a
   response model and stable component and relationship identifiers.
2. Read-only component-detail and neighborhood endpoints, including supported
   result limits and pagination/truncation semantics.
3. A read-only relationship event-history endpoint with exact filters and event
   response schema.
4. Upstream/downstream lineage endpoints with traversal bounds and explicit
   completeness/truncation metadata.
5. Any supported server-side filters and their query parameter validation.

No frontend filters, traversal, pagination, event histories, or graph controls
are advertised as functional until those contracts exist. Once provided, the
frontend can map actual directed relationships into a visualization; it must
preserve backend identifiers and must reject or report malformed relationships
rather than manufacture replacements.

An observed edge must not be presented as proof that specific content moved
unless the backend evidence supports that claim. Missing graph coverage is not
evidence of safety, and graph event status must remain distinct from an
authorization decision or a component-wide security state.

## Dependencies

No graph rendering dependency has been added. In particular, React Flow is not
installed because the backend currently returns no graph data to render.
