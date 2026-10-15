# AMGR API Contracts Specification

This document provides the reference documentation for the **AMGR Control Plane REST API**, implemented with FastAPI in `backend/app/api/authorization.py`.

---

## 1. General API Conventions & SDK Transport Behavior

### 1.1 Core API Conventions
- **Base URL Prefix**: Configured via the `API_V1_PREFIX` environment variable (defaults to `/api/v1`). For backwards compatibility and SDK convenience, endpoints are also mounted at the root (`/authorize`, `/approvals`, `/decisions`, `/health`).
- **Content Type**: `application/json` for all request bodies and responses.
- **Timestamp Standard**: UTC ISO-8601 strings (e.g., `2026-10-12T17:45:00.123456Z`).
- **Strict Validation**: All endpoints enforce Pydantic schemas with `extra="forbid"`. Unrecognized properties in request payloads produce an HTTP `422 Unprocessable Entity` response.
- **Fail-Closed Guarantee**: Any authorization evaluation error or unhandled failure results in an explicit denial (`BLOCK` / `deny`), never an accidental approval or synthetic `ALLOW`.

### 1.2 Data Format Standards
- **Correlation & Trace Identifiers**: Event IDs and flow trace IDs (`trace_id`, `flow_id`, `request_id`) are UUID-based or structured non-empty strings.
- **Timestamps**: Timezone-aware UTC datetimes serialized in ISO-8601 format.
- **Component References**: Source and destination components are typed with `ComponentRef` or structured subject objects.
- **Metadata**: Free-form dictionary keyed by strings (credentials, sensitive bearer tokens, and raw prompts strictly forbidden).
- **Authorization Outcomes**: Deterministic decision outcomes include canonical `ALLOW`, `BLOCK`, `REQUIRE_APPROVAL` (and SDK mapped literals `'allow'`, `'deny'`, `'review'`).

### 1.3 SDK Transport Behavior (`HttpTransport`)
The SDK client dispatches serialized payloads to the control plane:
- **Endpoint resolution**: Automatically normalizes base URLs to target API routes.
- **Transient error retries**: Automatically retries transient HTTP errors (`429`, `502`, `503`, `504`) with exponential backoff: `delay = retry_backoff_seconds * (2 ** attempt)`.
- **Non-retryable errors**: HTTP `501 Not Implemented` and client errors (`400`, `401`, `403`, `404`) fail immediately without futile retries.
- **Connection errors**: Socket, DNS, or timeout failures raise `TransportConnectionError`.
- **Credential scrubbing**: Authorization headers and bearer tokens are never formatted into exception strings or log messages.

---

## 2. Core Endpoints and Implementation Status

The table below reflects the verified implementation status of all control plane API endpoints:

| Endpoint | Method | Status | Owner | Description |
| :--- | :--- | :--- | :--- | :--- |
| `/api/v1/health` | `GET` | **Implemented** | Shared | Service liveness and readiness probe returning `{ "status": "ok" }`. |
| `/api/v1/authorize` | `POST` | **Implemented** | Member 3 | Deterministic runtime authorization evaluation against active policies. |
| `/api/v1/enforcement/authorize` | `POST` | **Implemented** | Member 3 | Member 1 SDK-compatible runtime enforcement endpoint with literal outcome mapping. |
| `/api/v1/authorization/decisions` | `POST` | **Implemented (Alias)** | Member 3 | Compatibility endpoint delegating directly to canonical `AuthorizationService.authorize`. |
| `/api/v1/approvals` | `GET` | **Implemented** | Member 3 | Human-in-the-loop approval listing with status/trace/policy filters and pagination. |
| `/api/v1/approvals/{id}` | `GET` | **Implemented** | Member 3 | Detailed inspection of individual approval requests. |
| `/api/v1/approvals/{id}/decision` | `POST` | **Implemented** | Member 3 | Human reviewer decision submission with AI/LLM exclusion and drift protection. |
| `/api/v1/approvals/{id}/can-proceed` | `GET` | **Implemented** | Member 3 | Lightweight execution gate check for pending/approved actions. |
| `/api/v1/approvals/{id}/cancel` | `POST` | **Implemented** | Member 3 | Human-initiated cancellation of pending approval requests. |
| `/api/v1/approvals/expire-stale` | `POST` | **Implemented** | Member 3 | Batch sweeping and transition of stale requests exceeding TTL expiration. |
| `/api/v1/decisions` | `GET` | **Implemented** | Member 3 | Searchable decision audit history with multi-parameter filtering. |
| `/api/v1/decisions/summary` | `GET` | **Implemented** | Member 3 | High-level decision statistics and aggregate metrics. |
| `/api/v1/decisions/{id}` | `GET` | **Implemented** | Member 3 | Inspection of specific persisted decision records. |
| `/api/v1/decisions/purge` | `POST` | **Implemented** | Member 3 | Time- or retention-based pruning of historical decision audit records. |
| `/api/v1/policies` | `GET`, `POST` | **Implemented** | Member 3 | Dynamic policy listing and creation. |
| `/api/v1/policies/{id}` | `GET`, `PUT`, `PATCH`, `DELETE` | **Implemented** | Member 3 | Policy retrieval, immutable version update, and soft deletion. |
| `/api/v1/policies/{id}/enable`, `/disable` | `POST` | **Implemented** | Member 3 | Activation state toggling with revision logging. |
| `/api/v1/policies/{id}/history`, `/revisions` | `GET` | **Implemented** | Member 3 | Immutable version history and audit revision retrieval. |
| `/api/v1/policies/{id}/rollback` | `POST` | **Implemented** | Member 3 | Rollback policy state to a previous version number. |
| `/api/v1/policies/simulate`, `/{id}/simulate` | `POST` | **Implemented** | Member 3 | Dry-run candidate policies against real or synthetic authorization traffic. |
| `/api/v1/policies/conflicts`, `/{id}/conflicts`| `POST`, `GET` | **Implemented** | Member 3 | Shadowing, contradiction, and redundancy conflict detection. |
| `/api/v1/flow-events` | `POST` | **Scaffolded / In-Progress** | Member 2 | Event ingestion endpoint (persists in flow table; graph integration ongoing). |
| `/api/v1/flow-events/batch` | `POST` | **Pending** | Member 2 | Batched event ingestion for efficient dispatch of multiple flow events. |
| `/api/v1/graph` | `GET` | **Scaffolded (HTTP 501)** | Member 2 | Graph query placeholder for node/edge topology. |
| `/api/v1/operator/chat` | `POST` | **Implemented** | Member 4 | AI Security Operator interactive chat and natural-language diagnosis. |

---

## 3. Implemented Endpoints Reference


### 3.1 Evaluate Runtime Authorization

Evaluates an inbound authorization request against active governance policies and records the evaluation in decision history.

- **Route**: `POST /api/v1/authorize` (and `POST /authorize`)
- **Status Code**: `200 OK`, `422 Unprocessable Entity`, `500 Internal Server Error` (fails closed)

#### Request Payload (`AuthorizationRequest`)
```json
{
  "trace_id": "trc_sec_audit_8819",
  "subject": "service_account_reporter",
  "action": "export_customer_data",
  "resource": "s3://prod-customer-backups",
  "context": {
    "environment": "production",
    "export_format": "csv",
    "record_count": 1500
  }
}
```

#### Response Payload (`AuthorizationDecision`)
```json
{
  "decision": "REQUIRE_APPROVAL",
  "policy_id": "pol_data_retention",
  "policy_version": 1,
  "matched_rule_id": "rule_large_export_approval",
  "matched_rule_ids": ["rule_large_export_approval"],
  "matched_policy_ids": ["pol_data_retention"],
  "reason_code": "RULE_MATCHED",
  "reason_message": "Rule 'rule_large_export_approval' matched with overriding REQUIRE_APPROVAL effect. Created human approval request 'appr_7c4f1a2e9b01'."
}
```

#### Special Headers
- `X-AMGR-Approval-ID`: Present when `decision == "REQUIRE_APPROVAL"`. Contains the generated unique ID of the created approval request (e.g., `appr_7c4f1a2e9b01`).

---

### 3.2 SDK-Compatible Runtime Enforcement (`/enforcement/authorize`)

Provides seamless compatibility with Member 1 Python SDK clients (`EnforcementClient`), translating between SDK transport structures and canonical AMGR policy representations.

- **Route**: `POST /api/v1/enforcement/authorize` (and `POST /enforcement/authorize`)
- **Status Codes**: `200 OK`, `422 Unprocessable Entity`, `500 Internal Server Error` (fails closed)

#### Request Payload (`SDKAuthorizationRequest`)
```json
{
  "request_id": "req-9a8b7c6d-5e4f-3a2b",
  "flow_id": "flow-11223344-5566-7788",
  "source": { "name": "analyst_agent", "type": "agent", "version": "1.0.0" },
  "destination": { "name": "payment_gateway", "type": "service", "version": "2.1.0" },
  "action": "refund_transaction",
  "metadata": { "amount_usd": 450.00, "customer_id": "cust_12345" },
  "timestamp": "2026-10-15T12:00:00.000000Z"
}
```

#### Response Payload (`SDKAuthorizationDecision`)
```json
{
  "request_id": "req-9a8b7c6d-5e4f-3a2b",
  "outcome": "review",
  "policy_ids": ["pol_financial_limits"],
  "reasons": ["Refunds over $250 require human financial controller approval."],
  "context": { "execution_gated": true }
}
```
*Note: Outcomes map directly to SDK literals: `ALLOW` -> `'allow'`, `BLOCK` -> `'deny'`, `REQUIRE_APPROVAL` -> `'review'`.*

---

### 3.3 Authorization Decisions Compatibility Alias (`/authorization/decisions`)

Backward-compatibility alias route ensuring existing clients and early-phase scaffolding calls safely evaluate policies rather than returning `501 Not Implemented`.

- **Route**: `POST /api/v1/authorization/decisions`
- **Behavior**: Delegates deterministically to `AuthorizationService.authorize`. Accepts `AuthorizationRequest`, returns `AuthorizationDecision`.

---

## 4. Human-in-the-Loop Approval Lifecycle & Persistence

Approvals provide an immutable, auditable gate for sensitive operations. All approval records are durably stored in the relational database (`approvals` table) with fail-closed security guarantees.

### 4.1 List Approval Requests

Retrieves approval requests, with multi-parameter filtering and pagination.

- **Route**: `GET /api/v1/approvals` (and `GET /approvals`)
- **Query Parameters**:
  - `status` (string, optional): One of `PENDING`, `APPROVED`, `REJECTED`, `EXPIRED`, `CANCELLED`.
  - `trace_id` (string, optional): Filter by correlation trace identifier.
  - `policy_id` (string, optional): Filter by triggering policy ID.
  - `limit` (integer, optional, default: 50, min: 1, max: 200): Maximum records to return.
  - `offset` (integer, optional, default: 0, min: 0): Number of records to skip.
- **Status Code**: `200 OK`

#### Response Payload (`list[ApprovalRequest]`)
```json
[
  {
    "id": "appr_7c4f1a2e9b01",
    "trace_id": "trc_sec_audit_8819",
    "policy_id": "pol_data_retention",
    "policy_version": 1,
    "matched_rule_id": "rule_large_export_approval",
    "original_decision": { ... },
    "request_payload": { ... },
    "status": "PENDING",
    "created_at": "2026-10-15T12:00:00.000000Z",
    "expires_at": "2026-10-15T12:30:00.000000Z",
    "decided_at": null,
    "decided_by": null,
    "decision_reason": null,
    "final_effect": "REQUIRE_APPROVAL"
  }
]
```

---

### 4.2 Get Approval Request by ID

Retrieves details for a specific approval request.

- **Route**: `GET /api/v1/approvals/{approval_id}` (and `GET /approvals/{approval_id}`)
- **Status Codes**:
  - `200 OK`: Request found.
  - `404 Not Found`: If `approval_id` does not exist.

---

### 4.3 Submit Human Approval Decision

Submits an explicit human reviewer decision (`APPROVE` or `REJECT`) for a pending approval request.

- **Route**: `POST /api/v1/approvals/{approval_id}/decision` (and `POST /approvals/{approval_id}/decision`)
- **Status Codes**:
  - `200 OK`: Decision recorded successfully.
  - `403 Forbidden`: Submitter is an AI/LLM entity, or reviewer identity is invalid.
  - `404 Not Found`: Approval request does not exist.
  - `409 Conflict`: Request is already finalized (single-decision finality) or has expired due to policy revision drift / TTL.

#### Request Payload (`ApprovalDecisionSubmission`)
```json
{
  "actor": "security_lead_sarah",
  "decision": "APPROVE",
  "reason": "Verified audit ticket #4910; export authorized for 24 hours.",
  "is_ai_or_llm": false
}
```

#### Response Payload (`ApprovalRequest`)
```json
{
  "id": "appr_7c4f1a2e9b01",
  "trace_id": "trc_sec_audit_8819",
  "policy_id": "pol_data_retention",
  "policy_version": 1,
  "matched_rule_id": "rule_large_export_approval",
  "status": "APPROVED",
  "created_at": "2026-10-15T12:00:00.000000Z",
  "expires_at": "2026-10-15T12:30:00.000000Z",
  "decided_at": "2026-10-15T12:05:22.000000Z",
  "decided_by": "security_lead_sarah",
  "decision_reason": "Verified audit ticket #4910; export authorized for 24 hours.",
  "final_effect": "ALLOW"
}
```

#### Safety Guardrails
- **AI/LLM Exclusion (`403 Forbidden`)**: If `is_ai_or_llm == true` or if `actor` begins with `ai:` or `llm:`, the submission is rejected. Automated systems cannot self-approve.
- **Drift Protection (`409 Conflict`)**: If the policy version or rule logic changes between request creation and decision submission, the approval request is invalidated.
- **Decision Finality (`409 Conflict`)**: Once finalized (`APPROVED`, `REJECTED`, `EXPIRED`, `CANCELLED`), an approval cannot be re-decided.

---

### 4.4 Check Protected Operation Permission (`can-proceed`)

Lightweight execution polling endpoint to verify whether a protected action associated with an approval ID is authorized to proceed.

- **Route**: `GET /api/v1/approvals/{approval_id}/can-proceed` (and `GET /approvals/{approval_id}/can-proceed`)
- **Status Codes**: `200 OK`, `404 Not Found`

#### Response Payload
```json
{
  "approval_id": "appr_7c4f1a2e9b01",
  "can_proceed": true,
  "status": "APPROVED",
  "final_effect": "ALLOW"
}
```
*Note: Returns `can_proceed: false` if status is `PENDING`, `REJECTED`, `EXPIRED`, `CANCELLED`, or if the governing policy was deactivated or updated after approval.*

---

### 4.5 Cancel Pending Approval Request (`cancel`)

Enables authorized human operators to explicitly cancel or withdraw a pending approval request.

- **Route**: `POST /api/v1/approvals/{approval_id}/cancel` (and `POST /approvals/{approval_id}/cancel`)
- **Status Codes**:
  - `200 OK`: Request transitioned to `CANCELLED`.
  - `403 Forbidden`: Automated AI/LLM entity attempted cancellation.
  - `404 Not Found`: Approval request does not exist.
  - `409 Conflict`: Request is already in a terminal state (`APPROVED`, `REJECTED`, `EXPIRED`, `CANCELLED`).

#### Request Payload (`ApprovalCancellationSubmission`)
```json
{
  "actor": "compliance_officer_dan",
  "reason": "Operation superseded by ticket #5021.",
  "is_ai_or_llm": false
}
```

---

### 4.6 Sweep Stale Approvals (`expire-stale`)

Batch sweeper endpoint for scanning pending approvals against their configured TTL expiration deadlines.

- **Route**: `POST /api/v1/approvals/expire-stale` (and `POST /approvals/expire-stale`)
- **Status Code**: `200 OK`

#### Response Payload
```json
{
  "swept_count": 3,
  "expired_ids": ["appr_old_01", "appr_old_02", "appr_old_03"]
}
```

---

### 4.7 Relational Persistence & Fail-Closed Invariants

Approvals utilize durable relational storage implemented via SQLAlchemy and Alembic:
- **Table Name**: `approvals` (created in Alembic migration `1252dd0605b1`, parent revision `8460d43aeaff`).
- **Data Model**: `ApprovalModel` storing request metadata, snapshots of original request and decision payloads as JSON, timestamps (`created_at`, `expires_at`, `decided_at`), and reviewer identity (`decided_by`).
- **Persistence Across Service Restarts**: Database-backed service instances reload and retrieve approvals created by earlier instances.
- **Fail-Closed Principle**: If database errors occur during record persistence, transactions are rolled back and an `ApprovalPersistenceError` is raised. Database errors **never** synthesize an approval or an `ALLOW` outcome.
- **Dual-Mode Operation**: Seamlessly supports persistent PostgreSQL/SQLite sessions in production/staging and in-memory operation for rapid isolated testing.

---

## 5. Governance Decision Audit & History

AMGR maintains an append-only audit trail of all runtime authorization evaluations.

### 5.1 Search Decision History
- **Route**: `GET /api/v1/decisions` (and `GET /decisions`)
- **Query Parameters**: `trace_id`, `decision` (`ALLOW`, `BLOCK`, `REQUIRE_APPROVAL`), `policy_id`, `limit`, `offset`.
- **Response**: `list[DecisionRecord]`

### 5.2 Decision Statistics Summary
- **Route**: `GET /api/v1/decisions/summary` (and `GET /decisions/summary`)
- **Response**: `DecisionSummary` containing aggregate counts (`total_count`, `allow_count`, `block_count`, `require_approval_count`) and recent decisions.

### 5.3 Get Individual Decision Record
- **Route**: `GET /api/v1/decisions/{decision_id}` (and `GET /decisions/{decision_id}`)
- **Response**: `DecisionRecord` detailing original evaluation parameters, timestamp, rule matches, and reason codes.

### 5.4 Purge Historical Decisions
- **Route**: `POST /api/v1/decisions/purge` (and `POST /decisions/purge`)
- **Request Payload (`DecisionPurgeRequest`)**: `{ "before_timestamp": "...", "retention_days": 90 }`
- **Response (`DecisionPurgeResponse`)**: `{ "purged_count": 1420, "remaining_count": 8500 }`

---

## 6. Policy Management, Simulation, & Verification

Implemented by Member 3, the Policy Engine provides deterministic lifecycle management, versioning, conflict detection, and pre-deployment simulation.

- **Policy CRUD**: `GET /api/v1/policies`, `POST /api/v1/policies`, `GET /api/v1/policies/{id}`, `PUT /api/v1/policies/{id}`, `DELETE /api/v1/policies/{id}`.
- **Activation Control**: `POST /api/v1/policies/{id}/enable`, `POST /api/v1/policies/{id}/disable`.
- **Immutable Version History & Rollback**: `GET /api/v1/policies/{id}/history`, `GET /api/v1/policies/{id}/revisions`, `POST /api/v1/policies/{id}/rollback`.
- **Pre-Deployment Simulation**: `POST /api/v1/policies/simulate`, `POST /api/v1/policies/{id}/simulate` (dry-run proposed policies against historical or synthetic requests).
- **Conflict & Shadow Detection**: `POST /api/v1/policies/conflicts`, `GET /api/v1/policies/{id}/conflicts` (detects unreachable rules, contradictions, and precedence overlaps).

---

## 7. Cross-Member Integration Boundaries & Implementation Status

To support clean cross-member coordination, integration boundaries are defined as follows:

| Surface | Owner | Implementation Status | Notes |
| :--- | :--- | :--- | :--- |
| **SDK Runtime Instrumentation** | Member 1 | **Implemented** | Python SDK client with `HttpTransport`, pre-operation hooks, and retry logic. |
| **Flow Event Ingestion (`/flow-events`)** | Member 2 | **In-Progress** | Basic ingestion route available; end-to-end relational graph persistence in development. |
| **Batch Event Ingestion (`/flow-events/batch`)**| Member 2 | **Pending** | Proposed batch ingestion endpoint pending backend implementation. |
| **Lineage & Provenance Graph (`/graph`)** | Member 2 | **Scaffolded (HTTP 501)** | Graph topology and query API placeholder. |
| **Policy Engine & Enforcement API** | Member 3 | **Implemented** | Fully verified deterministic policy evaluation, relational approvals, and decision audit. |
| **AI Security Operator (`/operator/chat`)** | Member 4 | **Implemented** | Natural-language query diagnosis and policy proposal generation. |
| **Governance Dashboard** | Member 5 | **In-Progress** | Frontend React/Vite dashboard consuming backend REST endpoints. |
