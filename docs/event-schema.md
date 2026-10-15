# Shared event schema

## Event contract

The shared event contract is a typed `FlowEvent` model used by the SDK and backend validation layer.

Required fields:

- `event_id`: unique identifier for this event.
- `application_id` and `environment`: required tenant/environment scope assigned by the trusted instrumentation configuration.
- `flow_id`: correlation key for the runtime flow as a whole.
- `correlation_id`: optional correlated request or session identifier.
- `parent_event_id`: optional reference to the initiating event.
- `timestamp`: UTC timestamp with timezone information.
- `source`: `ComponentRef` describing the origin component.
- `destination`: `ComponentRef` describing the target component.
- `action`: operation or event label carried by the event.
- `data_labels`: list of classification labels that describe sensitivity or data properties.
- `metadata`: free-form metadata for extension and observability.
- `status`: business or processing status, such as `observed` or `blocked`.
- `duration_ms`: optional measured duration in milliseconds.

`data_labels` is preserved as captured; the event schema does not identify these labels as input or output labels. `metadata` is opaque extension data: transformation metadata may be retained there only when instrumentation supplies it, but the backend does not interpret or derive transformations. Events must include a scope authorized by backend identity middleware before ingestion.

## `ComponentRef`

`ComponentRef` captures identity and type for runtime nodes. It includes:

- `component_id`: stable identifier for the component
- `component_type`: category such as agent, model, api, db, or tool
- `name`: optional human-readable name

## Compatibility guideline

The SDK and backend share the same contract semantics, but each may own separate objects internally. The goal is compatibility without code generation: shared field names, clear typing, and explicit versioning when a contract changes.
