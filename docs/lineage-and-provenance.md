# Lineage and provenance

## Provenance semantics

Lineage is built only from fields on a persisted `FlowEvent`. The raw recorded
values remain authoritative; service responses organize them but do not infer
missing relationships.

The response distinguishes:

- `explicit_parent_event`: the child event's stored `parent_event_id` points to
  another event found in the same authorized application/environment.
- `observed_interaction`: one recorded event names its source and destination
  components. This confirms an observed interaction, not that data was
  propagated between those components.
- `unresolved_parent_event_ids`: parent references that could not be resolved
  within the authorized scope or returned result. This does not disclose
  whether an identifier exists outside that scope.
- `completeness: unknown`: the requested traversal completed across available
  explicit records, but instrumentation coverage cannot be established.
- `completeness: incomplete`: a known parent reference was unresolved or a
  traversal/page limit prevented returning all known records.

Correlation IDs group events only. They are never used as causal edges.
Timestamps order returned events but do not establish causality.

## Event and relationship fields

The stored event preserves its UUID event/flow/correlation/parent IDs, UTC
timestamp, application and environment scope, source and destination
component references, action, data-label list, metadata dictionary, status,
and duration. Parent IDs are not database foreign keys so out-of-order
ingestion is supported.

`data_labels` has no input/output direction in the instrumentation schema.
The response returns that list exactly as recorded. `metadata` is opaque JSON;
transformation details are retained only if instrumentation places them
there. No label propagation, declassification, transformation interpretation,
or field/record/byte-level provenance is inferred.

## Persistence and ingestion

`POST /api/v1/flow-events` persists the event in the `flow_events` table.
Required event-scope fields `application_id` and `environment` must match a
scope supplied by trusted authentication middleware on
`request.state.operator_identity`. An identical event ID and payload is
idempotent and returns `200` with `duplicate: true`; reusing an event ID in the same scope with
different content returns `409`. Event IDs are unique within the
application/environment composite scope. New events return `201`.

Apply the schema before running the backend:

```powershell
cd backend
alembic upgrade head
```

No graph or derived-lineage table is created; lineage is queried from the
authoritative event table.

## API routes

All routes are under the configured API prefix (default `/api/v1`).
Every route requires an authenticated identity with authorized scopes. If
exactly one scope is authorized, callers may omit the scope query parameters.
If multiple scopes are authorized, callers must provide both
`application_id` and `environment`; the pair is checked against the identity.
Queries and event writes always apply both scope columns.

| Method and path | Query parameters | Behavior |
| --- | --- | --- |
| `POST /api/v1/flow-events` | JSON `FlowEvent`, including application/environment | Validates authorized scope and stores the exact event. |
| `GET /api/v1/lineage/events/{event_id}` | `direction` (`upstream`, `downstream`, `both`, default `both`); `max_depth` (1–8, default 3); `limit` (1–100, default 50); optional scope pair | Traverses explicit parent links only. Returns 404 when the event is not found within the authorized scope. |
| `GET /api/v1/lineage/flows/{flow_id}` | `limit` (1–100, default 50); `offset` (>=0); optional scope pair | Returns chronologically ordered events for the flow, with stable event-ID tie ordering. No causal relation is inferred from the shared flow ID. |
| `GET /api/v1/lineage/components/{component_id}` | `direction` (`upstream`, `downstream`, `both`, default `both`); `limit` (1–100, default 50); `offset` (>=0); optional scope pair | Returns one-hop interactions where the component is the recorded destination (`upstream`), source (`downstream`), or either (`both`). It does not traverse component relationships. |

Event traversal uses breadth-first bounded parent/child lookups, deduplicates
events, and avoids repeating cycles. The global event count is capped by
`limit`; depth is capped by `max_depth`. Flow/component list routes fetch at
most `limit + 1` rows to set `truncated` and `next_offset`.

## Response schema

Each response includes:

- `events`: original validated event fields, including raw labels and metadata.
- `nodes`: typed event/component nodes using the recorded event and component
  identifiers (no generated identifiers).
- `edges`: `explicit_parent_event` edges with evidence basis
  `recorded_parent_event_id`, and `observed_interaction` edges with evidence
  basis `recorded_event_components`.
- `unresolved_parent_event_ids`, `completeness`, `truncated`, and
  `truncation_reasons`.
- `traversal`: direction, depth/limit, and pagination offsets where applicable.
- `scope`: the authorized application/environment used for the query.

## Scope and authorization limitation

Scope checks are implemented and shared with operator authorization logic.
However, this repository's FastAPI app currently does not register middleware
that populates `request.state.operator_identity`. Until deployment supplies
that trusted identity middleware, event ingestion and lineage endpoints return
401. The API intentionally does not permit unauthenticated caller-selected
scope as an alternative.

## Member integration and known gaps

- **Instrumentation / Member 1:** SDK `FlowEvent` now requires application and
  environment scope. Instrumentation still needs to assign those values from
  trusted configuration. It captures a single `data_labels` list and does not
  define input/output labels or transformation semantics.
- **AI Security Operator / Member 4:** component lineage tools query the same
  scoped repository and return the lineage response's observed interactions,
  unresolved references, and completeness status. Component tool calls are
  one-hop; the `traversal_depth` tool parameter cannot imply causal component
  traversal.
- **Dashboard / Member 5:** graph pages call the component-lineage API when a
  selected graph node provides a stable component ID. Graph snapshot remains
  an independent missing/501 dependency, so no component can currently be
  selected from a live graph in this repository.
- **Persistence/deployment:** run the Alembic migration, configure the database,
  and install trusted authentication middleware before using the new routes.

No origin component is labeled as the ultimate data origin: the schema only
provides one event's source/destination and optional parent event ID.
