# AMGR API Contracts Specification

This document provides the reference documentation for the **AMGR Control Plane REST API**, implemented with FastAPI in `backend/app/api/authorization.py`.

---

## 1. General API Conventions

- **Base URL Prefix**: Configured via the `API_V1_PREFIX` environment variable (defaults to `/api/v1`). For backwards compatibility and SDK convenience, endpoints are also mounted at the root (`/authorize`, `/approvals`).
- **Content Type**: `application/json` for all request bodies and responses.
- **Timestamp Standard**: UTC ISO-8601 strings (e.g., `2026-10-12T17:45:00.123456Z`).
- **Strict Validation**: All endpoints enforce Pydantic schemas with `extra="forbid"`. Unrecognized properties in request payloads produce a `422 Unprocessable Entity` response.

---

## 2. Implemented Endpoints Reference

### 2.1 Evaluate Runtime Authorization

Evaluates an inbound authorization request against active governance policies and records the evaluation in decision history.

- **Route**: `POST /api/v1/authorize` (and `POST /authorize`)
- **Status Code**: `200 OK`

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
  "matched_rule_id": "rule_large_export_approval",
  "matched_rule_ids": ["rule_large_export_approval"],
  "matched_policy_ids": ["pol_data_retention"],
  "reason_code": "RULE_MATCHED",
  "reason_message": "Rule 'rule_large_export_approval' matched with overriding REQUIRE_APPROVAL effect. Created human approval request 'appr_7c4f1a2e9b01'.",
  "evaluated_at": "2026-10-12T18:00:00.000000Z"
}
```

#### Special Headers
- `X-AMGR-Approval-ID`: Present when `decision == "REQUIRE_APPROVAL"`. Contains the generated unique ID of the created approval request (e.g., `appr_7c4f1a2e9b01`).

---

### 2.2 List Approval Requests

Retrieves governance approval requests, with optional status filtering.

- **Route**: `GET /api/v1/approvals` (and `GET /approvals`)
- **Query Parameters**:
  - `status` (string, optional): One of `PENDING`, `APPROVED`, `REJECTED`, `EXPIRED`.
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
    "original_decision": {
      "decision": "REQUIRE_APPROVAL",
      "policy_id": "pol_data_retention",
      "matched_rule_id": "rule_large_export_approval",
      "matched_rule_ids": ["rule_large_export_approval"],
      "matched_policy_ids": ["pol_data_retention"],
      "reason_code": "RULE_MATCHED",
      "reason_message": "Bulk data export requires compliance sign-off.",
      "evaluated_at": "2026-10-12T18:00:00.000000Z"
    },
    "request_payload": {
      "trace_id": "trc_sec_audit_8819",
      "subject": "service_account_reporter",
      "action": "export_customer_data",
      "resource": "s3://prod-customer-backups",
      "context": {
        "environment": "production",
        "record_count": 1500
      }
    },
    "status": "PENDING",
    "created_at": "2026-10-12T18:00:00.000000Z",
    "decided_at": null,
    "decided_by": null,
    "decision_reason": null,
    "final_effect": "REQUIRE_APPROVAL"
  }
]
```

---

### 2.3 Get Approval Request by ID

Retrieves details for a specific approval request.

- **Route**: `GET /api/v1/approvals/{approval_id}` (and `GET /approvals/{approval_id}`)
- **Status Codes**:
  - `200 OK`: Request found.
  - `404 Not Found`: If `approval_id` does not exist.

#### Error Response (`404 Not Found`)
```json
{
  "detail": "Approval request 'appr_missing' not found."
}
```

---

### 2.4 Submit Human Approval Decision

Submits an explicit human reviewer decision (`APPROVE` or `REJECT`) for a pending approval request.

- **Route**: `POST /api/v1/approvals/{approval_id}/decision` (and `POST /approvals/{approval_id}/decision`)
- **Status Codes**:
  - `200 OK`: Decision recorded successfully.
  - `403 Forbidden`: Submitter is an AI/LLM entity, or not authorized.
  - `404 Not Found`: Approval request does not exist.
  - `409 Conflict`: Request is already finalized (single-decision finality) or has expired due to policy revision drift.

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
  "original_decision": { ... },
  "request_payload": { ... },
  "status": "APPROVED",
  "created_at": "2026-10-12T18:00:00.000000Z",
  "decided_at": "2026-10-12T18:05:22.100000Z",
  "decided_by": "security_lead_sarah",
  "decision_reason": "Verified audit ticket #4910; export authorized for 24 hours.",
  "final_effect": "ALLOW"
}
```

#### Rejection by Automated Entities (`403 Forbidden`)
If `is_ai_or_llm == true` or if `actor` begins with `ai:` or `llm:`:
```json
{
  "detail": "Automated AI/LLM entities are forbidden from approving governance requests. Explicit human review is required."
}
```

---

### 2.5 Check Protected Operation Permission (`can-proceed`)

Lightweight polling endpoint to verify whether a protected action associated with an approval ID is authorized to proceed.

- **Route**: `GET /api/v1/approvals/{approval_id}/can-proceed` (and `GET /approvals/{approval_id}/can-proceed`)
- **Status Codes**:
  - `200 OK`
  - `404 Not Found`: If `approval_id` does not exist.

#### Response Payload
```json
{
  "approval_id": "appr_7c4f1a2e9b01",
  "can_proceed": true,
  "status": "APPROVED",
  "final_effect": "ALLOW"
}
```

- Returns `can_proceed: false` if status is `PENDING`, `REJECTED`, or `EXPIRED`.
- Returns `can_proceed: false` if the governing policy was modified or disabled after approval was granted.

---

### 2.6 Health Check

Service liveness and readiness probe.

- **Route**: `GET /health` and `GET /api/v1/health`
- **Status Code**: `200 OK`

#### Response Payload
```json
{
  "status": "ok"
}
```

---

## 3. Explicit Implementation Status & Phase 2 Roadmap

To maintain strict alignment with the codebase, the following surfaces are documented regarding their implementation status:

| Surface / Feature | Status | Notes |
| :--- | :--- | :--- |
| **Runtime Authorization API** (`/authorize`) | **Implemented (Phase 1)** | Fully functional, deterministic, backed by persistence and approval workflows. |
| **Approval Lifecycle API** (`/approvals/*`) | **Implemented (Phase 1)** | Fully functional with human validation and drift protection. |
| **Health Probes** (`/health`, `/api/v1/health`) | **Implemented (Phase 1)** | Available for container orchestration and monitors. |
| **Dynamic Policy CRUD API** (REST routes for policy create/update) | **Unimplemented (Phase 2)** | Policies are managed via `PolicyRepository` in code/tests; REST endpoints for policy management are scheduled for Phase 2. |
| **Event Ingestion & Provenance Graph API** | **Unimplemented (Phase 2)** | Event ingestion and lineage graph queries are designated for Phase 2/3. |
| **Security Dashboard Live Wiring** | **Unimplemented (Phase 2)** | Frontend dashboard integration is part of Phase 2. |
