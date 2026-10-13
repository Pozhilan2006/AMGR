# API contracts

## Versioning

All backend routes are served under the `/api/v1` prefix.

## Core endpoints and implementation status

| Endpoint | Method | Status | Owner | Description |
| :--- | :--- | :--- | :--- | :--- |
| `/api/v1/health` | `GET` | **Implemented** | Shared | Health check endpoint returning `{ "status": "ok" }`. |
| `/api/v1/flow-events` | `POST` | **Scaffolded (HTTP 501)** | Member 2 | Event ingestion endpoint. Currently returns `501 Not Implemented` in starter backend pending graph persistence. |
| `/api/v1/flow-events/batch` | `POST` | **Proposed / Pending** | Member 2 | Proposed batched event ingestion for efficient dispatch of multiple flow events. |
| `/api/v1/graph` | `GET` | Scaffolded (HTTP 501) | Member 2 | Graph query placeholder for node/edge topology. |
| `/api/v1/policies` | `GET` | Scaffolded (HTTP 501) | Member 3 | Policy listing placeholder. |
| `/api/v1/authorization/decisions` | `POST` | Scaffolded (HTTP 501) | Member 3 | Pre-operation authorization evaluation placeholder. |
| `/api/v1/approvals` | `GET` | Scaffolded (HTTP 501) | Member 3 | Human-in-the-loop approval listing placeholder. |

## SDK Transport behavior (`HttpTransport`)

The SDK client dispatches serialized `FlowEvent` payloads to `/api/v1/flow-events`:
- **Endpoint resolution**: Automatically normalizes base URLs to target `/flow-events`.
- **Transient error retries**: Automatically retries transient HTTP errors (`429`, `502`, `503`, `504`) with exponential backoff: `delay = retry_backoff_seconds * (2 ** attempt)`.
- **Non-retryable errors**: HTTP `501 Not Implemented` and client errors (`400`, `401`, `403`, `404`) fail immediately without futile retries.
- **Connection errors**: Socket, DNS, or timeout failures raise `TransportConnectionError`.
- **Credential scrubbing**: Authorization headers and bearer tokens are never formatted into exception strings or log messages.

## Integration dependencies & limitations

1. **Pending backend batch endpoint (`POST /api/v1/flow-events/batch`)**:
   - The SDK supports sequential batch dispatch via `HttpTransport.send_batch` with partial-failure tracking (`BatchTransportError`).
   - A dedicated backend batch ingestion endpoint is pending implementation by Member 2 / Backend team.
2. **Backend event ingestion persistence**:
   - The backend `POST /api/v1/flow-events` route is currently a placeholder returning `501 Not Implemented`. End-to-end database storage of ingested events is pending Member 2 implementation.

## Shared schema conventions

- Event IDs and flow IDs are UUID-based.
- Timestamps are timezone-aware UTC datetimes.
- Source and destination components are typed with `ComponentRef`.
- Metadata is a free-form dictionary keyed by strings (credentials and raw prompts strictly forbidden).
- Authorization decisions include explicit `allow`, `deny`, or `review` outcomes.

## Compatibility policy

The Python SDK and backend Pydantic models use the same field names and semantic intent. If they diverge over time, the project should keep both models compatible by default and prefer explicit versioning of contract changes rather than introducing a complex code generation workflow at this stage.
