# API contracts

## Versioning

All backend routes are served under the `/api/v1` prefix.

## Core endpoints

- `GET /api/v1/health` — returns API health status.
- `POST /api/v1/flow-events` — event ingestion endpoint placeholder.
- `GET /api/v1/graph` — graph query placeholder.
- `GET /api/v1/policies` — policy listing placeholder.
- `POST /api/v1/authorization/decisions` — authorization placeholder.
- `GET /api/v1/approvals` — approval listing placeholder.

## Shared schema conventions

- Event IDs and flow IDs are UUID-based.
- Timestamps are timezone-aware UTC datetimes.
- Source and destination components are typed with `ComponentRef`.
- Metadata is a free-form dictionary keyed by strings.
- Authorization decisions include explicit `allow`, `deny`, or `review` outcomes.

## Compatibility policy

The Python SDK and backend Pydantic models use the same field names and semantic intent. If they diverge over time, the project should keep both models compatible by default and prefer explicit versioning of contract changes rather than introducing a complex code generation workflow at this stage.
