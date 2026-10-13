# Shared event schema

## Event contract

The shared event contract is a typed `FlowEvent` model used by the SDK and backend validation layer.

Required fields:

- `event_id`: unique identifier for this event.
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

### Field semantics and optionality

| Field | Type | Required / Default | Contract Semantics |
| :--- | :--- | :--- | :--- |
| `event_id` | `UUID` | Generated (`uuid4`) | Unique identifier for this individual event. |
| `flow_id` | `UUID` | **Required** | Correlation key for the overall runtime flow or execution tree. |
| `correlation_id` | `UUID \| None` | Optional (`None`) | Correlated request, trace, or session identifier if present; explicitly `None` otherwise. |
| `parent_event_id` | `UUID \| None` | Optional (`None`) | Reference to initiating event in causal chain. Never invent parent relationships. |
| `timestamp` | `datetime` (UTC) | Generated (now UTC) | Timezone-aware UTC timestamp. Naive timestamps are rejected. |
| `source` | `ComponentRef` | **Required** | Origin component initiating the action. Never invent mock components. |
| `destination` | `ComponentRef` | **Required** | Target component receiving the action. |
| `action` | `str` | **Required** (non-empty) | Operation or event label (e.g., `model.inference`, `tool.execute`, `api.request`). |
| `data_labels` | `list[str]` | Optional (`[]`) | Classification labels (e.g. `pii`, `confidential`). Never invent classifications. |
| `metadata` | `dict[str, Any]` | Optional (`{}`) | Extensible payload for observability. Secrets and raw credentials are forbidden. |
| `status` | `str` | Optional (`"observed"`) | Business or processing status (e.g., `observed`, `blocked`). |
| `duration_ms` | `float \| None` | Optional (`None`) | Measured execution duration in milliseconds (must be non-negative, `ge=0`). |

`FlowEvent` enforces `extra="forbid"` on both SDK and backend validation layers to prevent unagreed top-level fields from entering the runtime.

## `ComponentRef`

`ComponentRef` captures identity and category for runtime nodes. It enforces `extra="forbid"` and `frozen=True`.

### Fields and validation rules
- `component_id`: stable identifier for the component (e.g., `agent-1`, `gpt-4o`, `db-main`). Must be a non-empty string; stripped of leading/trailing whitespace.
- `component_type`: category category string. Must be a non-empty string; stripped of leading/trailing whitespace.
- `name`: optional human-readable name (or `None`).

### Baseline component categories (`STANDARD_COMPONENT_TYPES`)
- `user`: human end-user submitting requests or prompts.
- `frontend` / `ui`: web or client interface dispatching requests.
- `service` / `api`: backend services, REST/gRPC endpoints, or internal microservices.
- `db` / `database`: storage systems, SQL databases, or vector stores.
- `agent`: autonomous agents or workflow coordinators.
- `model`: foundation models or LLM inference endpoints.
- `tool`: local function tools, code sandboxes, or external plugins.
- `external_service`: third-party hosted services or APIs outside the trusted boundary.

### Convenience factory classmethods
The SDK provides typed constructors on `ComponentRef` for all architectural tiers:
```python
user = ComponentRef.user("user-101", name="Alice")
ui = ComponentRef.frontend("web-client", name="Dashboard")
backend = ComponentRef.service("core-api", name="FastAPI Backend")
database = ComponentRef.database("postgres-main", name="PostgreSQL")
agent = ComponentRef.agent("research-agent", name="Supervisor")
tool = ComponentRef.tool("search-api", name="Web Search")
```

---

## Runtime Capture API and Flow Lineage

### Lineage preservation semantics
AMGR tracks causal provenance across multi-hop agent architectures using three correlation keys:
1. `flow_id`: Root identifier for the entire execution tree or user transaction. Created once at the root scope and preserved across all child operations.
2. `correlation_id`: Optional request or session identifier correlating external client requests with the flow.
3. `parent_event_id`: UUID pointing to the immediate initiating or enclosing event.
   - For root interactions (e.g. User -> Frontend): `parent_event_id = None`.
   - For nested operations (Backend -> AI Agent -> Child Agent -> Grandchild Agent -> Tool): `parent_event_id` is automatically set to the caller's `event_id`. Never invent speculative relationships.

Lineage is automatically maintained across synchronous and asynchronous execution using ambient `FlowContext` and Python `contextvars`.

### Capture interfaces

#### 1. Function Decorator (`@capture`)
Instruments synchronous or asynchronous functions, measuring duration in milliseconds and establishing child context scopes:
```python
@capture(
    action="agent.delegate",
    destination=ComponentRef.agent("worker-agent"),
    source=ComponentRef.agent("supervisor-agent"),
    data_labels=["task_spec"],
)
def run_worker(subtask: str) -> dict[str, Any]:
    return {"result": f"Completed {subtask}"}
```

#### 2. Block-Level Context Manager (`capture_span`)
Instruments procedural blocks such as database queries or network dispatches:
```python
with capture_span(
    action="db.query",
    destination=ComponentRef.database("postgres"),
    source=ComponentRef.service("core-backend"),
    data_labels=["sql_query"],
    metadata={"table": "users", "rows_returned": 1},
):
    results = db.execute("SELECT * FROM users WHERE id = :id", id=user_id)
```

#### 3. Procedural Operation Recorder (`recorder.record_operation`)
Direct helper on `EventRecorder` to construct and dispatch a validated event inheriting ambient lineage:
```python
recorder.record_operation(
    action="http.request",
    destination=ComponentRef.api("payment-service"),
    duration_ms=45.2,
    metadata={"status_code": 200},
)
```

---

## Security and telemetry hygiene

- **Metadata only**: Telemetry captures operational metadata only (operation labels, duration, classification labels, execution status). Never capture raw prompts, database records, payload bodies, API keys, passwords, bearer tokens, or secrets (`DISALLOWED_METADATA_KEYS` rejected or redacted at the SDK boundary).
- **Lineage preservation**: Causal edges (`parent_event_id`), trace keys (`flow_id`), and correlation keys (`correlation_id`) are automatically propagated across nested agent and tool scopes. Speculative or invented parent relationships are forbidden.
- **Fail-safe capture**: Runtime telemetry failures log non-intrusively by default and never interrupt application business logic or expose secrets, unless strict error raising (`raise_transport_errors=True` or `strict=True`) is explicitly enabled.
- **Metadata sanitization**: The SDK provides `sanitize_metadata(metadata, redact=True)` to mask sensitive fields as `"[REDACTED]"`.

---

## Compatibility guideline

The SDK and backend share the same contract semantics, but each owns separate objects internally. The goal is compatibility without code generation: shared field names, clear typing, and explicit versioning when a contract changes.

---

## Proposals under review (Requires AMGR Team Approval)

The following items are proposed extensions for discussion with Members 2, 3, 4, and 5 and are **not** finalized shared schema changes:
1. **Component Category Enum**: Propose codifying `component_type` as a shared `Enum`/`Literal` across SDK, Backend, and Frontend once Members 2 and 5 confirm all graph visualization node categories.
2. **Standardize `database` vs. `db`**: Propose accepting `database` as a formal alias alongside `db`.
3. **Batched Ingestion Endpoint**: Propose a dedicated `POST /api/v1/flow-events/batch` endpoint on backend for efficient multi-event dispatch. (Currently pending backend implementation; SDK operates via sequential loop).
4. **User and Frontend Component Categories**: Propose formalizing `user` and `frontend` (or `ui`) in `STANDARD_COMPONENT_TYPES` across Backend and Frontend models to support full end-to-end data-flow tracking (User -> Frontend -> Backend -> DB -> AI Agents -> External Tools).
5. **Hierarchical Causal Tree Semantics**: Propose adopting tree-based causal propagation (where child agent invocations and tool calls point `parent_event_id` to their enclosing caller/delegation event) to facilitate acyclic lineage derivation in Member 2's graph engine.
