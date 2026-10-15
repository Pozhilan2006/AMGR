# Shared event schema

The SDK and backend use the same strict `FlowEvent` contract. Unknown top-level
fields are rejected. Scope is required on each event and must be configured by the
instrumentation owner and authorized by backend identity middleware.

| Field | Type | Required / default | Meaning |
| --- | --- | --- | --- |
| `event_id` | UUID | Generated with `uuid4` | Event identity; deduplicated within application and environment. |
| `application_id` | string, 1–200 chars | Required | Authorized application boundary. |
| `environment` | string, 1–100 chars | Required | Deployment environment. |
| `flow_id` | UUID | Required | Runtime flow or execution tree identifier. |
| `correlation_id` | UUID or `null` | Optional | Related request or session identifier. |
| `parent_event_id` | UUID or `null` | Optional | Explicit initiating event, when known. |
| `timestamp` | timezone-aware datetime | Current UTC time | Time the event occurred; normalized to UTC. |
| `source` | `ComponentRef` | Required | Component that initiated the interaction. |
| `destination` | `ComponentRef` | Required | Component that received the interaction. |
| `action` | string, 1–500 chars | Required | Operation label, such as `model.inference`. |
| `data_labels` | list of strings | `[]` | Caller-supplied sensitivity or data labels. |
| `metadata` | string-keyed object | `{}` | Safe operational metadata; secrets and raw prompts/payloads must be redacted. |
| `status` | string, 1–100 chars | `observed` | Event status, such as `completed`, `failed`, or `blocked`. |
| `duration_ms` | nonnegative number or `null` | `null` | Measured operation duration, when available. |

`ComponentRef` contains `component_id` (1–200 chars), `component_type` (1–100
chars), and optional `name` (up to 200 chars). The SDK also provides factories for
common types such as agents, models, tools, services, APIs, and databases.

## Scope configuration

Set `AMGR_APPLICATION_ID` and `AMGR_ENVIRONMENT` together when using the default
SDK client. They are copied into captured events; the SDK does not invent a scope.
When constructing an `EventRecorder` directly, pass both values explicitly. The
backend compares the event scope with the trusted identity on the request and
rejects unauthorized scopes.

## Lineage and event persistence

Events with the same `(event_id, application_id, environment)` are idempotent only
when their contract fields match. Reusing that scoped ID with different content
returns a conflict. Parent links are accepted only within the same scope and flow.
If a referenced parent has not arrived, AMGR preserves the explicit unresolved
parent ID and marks its lineage `unknown_or_incomplete`; it links the event after
the parent arrives. It never infers parentage from timestamps or graph adjacency.

Metadata is sanitized at the SDK boundary and checked again by the backend. Keys
for credentials, secrets, raw prompts, and payload bodies must be omitted or have
their values replaced with `[REDACTED]`. Metadata is not a place to store event
payloads or database records. Data labels and transformations are retained only
when instrumentation supplies them; the backend does not fabricate either.

## Capture

`@capture`, `capture_span`, and `EventRecorder` preserve `flow_id`,
`correlation_id`, and `parent_event_id` through the SDK's ambient flow context.
Each captured event still requires an explicitly configured application and
environment scope. If context or scope is unavailable, the SDK surfaces that
configuration error instead of assigning a made-up value.
