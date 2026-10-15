# AMGR Runtime Authorization Specification

This document defines the runtime authorization protocol, evaluation lifecycle, schema contracts, and human approval workflow integration for the **Autonomous Multi-Agent Governance Runtime (AMGR)**.

---

## 1. Runtime Enforcement Boundary

AMGR acts as a deterministic control plane intercepting interactions between AI agents, execution runtimes, models, and system tools.

```
+------------------+       AuthorizationRequest       +--------------------------+
|  Instrumented    | -------------------------------> |   AMGR Enforcement API   |
|  Agent / Tool    |                                  |   (AuthorizationService) |
+------------------+                                  +--------------------------+
         ^                                                          |
         |                                                 1. Verify Prior Approvals
         |                                                 2. Evaluate Active Policies
         |                                                 3. Record Decision History
         |                                                          |
         |             AuthorizationDecision                        v
         +--------------------------------------------+   +--------------------+
         |  ALLOW            -> Proceed execution     |   | DecisionRepository |
         |  BLOCK            -> Halt execution        |   | (PolicyDecisionModel)|
         |  REQUIRE_APPROVAL -> Suspend for human     |   +--------------------+
```

---

## 2. Authorization Request Schema (`AuthorizationRequest`)

All callers must present an immutable `AuthorizationRequest` at the runtime boundary:

```json
{
  "trace_id": "trc_agent_session_9182",
  "subject": {
    "agent_id": "financial_analyst_v2",
    "role": "analyst",
    "department": "risk"
  },
  "action": "execute_tool",
  "resource": "database://sec_filings_read",
  "context": {
    "environment": "production",
    "risk_score": 42,
    "classification": "confidential"
  }
}
```

### Fields & Validation Rules
- `trace_id` (string, required): Correlation identifier linking this evaluation to the SDK and multi-agent interaction flow. Must be a non-empty string.
- `subject` (dict or string, required): Identity or component reference initiating the request (e.g., string ID or structured object describing the caller).
- `action` (string, required): The operation being attempted (e.g., `execute_tool`, `query_db`, `invoke_model`). Non-empty string.
- `resource` (string, required): Target resource identifier, URI, or tool name (e.g., `tool://stripe/refund`, `db://customer_vault`). Non-empty string.
- `context` (dict, optional): Structured execution attributes, environment variables, security tags, or request parameters. Defaults to `{}`.
- `extra="forbid"`: Unknown extra top-level fields are rejected with a validation error.

### Context Flattening (`to_eval_context`)
During policy evaluation, the request is flattened into an attribute mapping:
- Direct top-level access: `eval_ctx["action"]`, `eval_ctx["resource"]`, `eval_ctx["subject"]`, `eval_ctx["trace_id"]`, `eval_ctx["context"]`.
- The contents of `context` are unpacked into `eval_ctx` root for concise paths (e.g., `context.risk_score` or `risk_score`).

---

## 3. Authorization Decision Schema (`AuthorizationDecision`)

Every evaluation yields a deterministic `AuthorizationDecision`:

```json
{
  "decision": "BLOCK",
  "policy_id": "pol_data_exfiltration_guard",
  "matched_rule_id": "rule_block_public_export",
  "matched_rule_ids": ["rule_block_public_export"],
  "matched_policy_ids": ["pol_data_exfiltration_guard"],
  "reason_code": "RULE_MATCHED",
  "reason_message": "Rule 'rule_block_public_export' matched with overriding BLOCK effect.",
  "evaluated_at": "2026-10-12T17:45:00.123456Z"
}
```

### Fields
- `decision` (`PolicyEffect`): Final outcome:
  - `ALLOW`: Permitted to proceed.
  - `BLOCK`: Denied and halted at the boundary.
  - `REQUIRE_APPROVAL`: Suspended pending explicit human review.
- `policy_id` (string or `null`): Identifier of the deciding policy. `null` if no policies matched or on global fallback.
- `matched_rule_id` (string or `null`): Identifier of the primary rule that dictated the outcome.
- `matched_rule_ids` (list of strings): Identifiers of all rules that evaluated to `True` during execution.
- `matched_policy_ids` (list of strings): Identifiers of all policies that matched during execution.
- `reason_code` (`ReasonCode`): Machine-readable explanation code.
- `reason_message` (string): Human-readable justification explaining the outcome.
- `evaluated_at` (datetime): UTC ISO-8601 timestamp when evaluation completed.

---

## 4. Standardized Reason Codes (`ReasonCode`)

| Reason Code | Meaning | Example Trigger |
| :--- | :--- | :--- |
| `RULE_MATCHED` | One or more rules matched and dictated the decision. | A blocking rule or permission rule condition evaluated to `True`. |
| `DEFAULT_FALLBACK` | No rules matched, or no active policies were provided; safe fallback applied. | Zero rules matched in policy; defaulted to `BLOCK`. |
| `POLICY_DISABLED` | The governing policy is disabled (`enabled=false`). | Attempted to evaluate a deactivated policy; fails closed to `BLOCK`. |
| `MISSING_CONTEXT` | Required security context attributes were absent from the request. | Condition required `context.user_id` but it was not provided. |
| `INVALID_REQUEST` | Inbound payload was malformed or referenced an invalid approval ID. | `context.approval_id` was provided but the approval request does not exist. |
| `CONFLICT_RESOLVED` | Multiple equal-priority policies matched with conflicting effects and were resolved via conflict algorithm. | Policy A returned `BLOCK` and Policy B returned `ALLOW` at priority 100; resolved to `BLOCK`. |

---

## 5. Human Approval Workflow Integration

When an evaluation renders `REQUIRE_APPROVAL`, AMGR automatically initiates an asynchronous human-in-the-loop review workflow.

```
1. Request -> POST /authorize -> Decision: REQUIRE_APPROVAL
                                  Header: X-AMGR-Approval-ID: appr_abc123
                                  State: PENDING

2. Human Reviewer -> POST /approvals/appr_abc123/decision
                     Body: { "actor": "security_officer_alice", "decision": "APPROVE" }
                     State: APPROVED

3. Resumed Request -> POST /authorize
                     Body: { ..., "context": { "approval_id": "appr_abc123" } }
                     Outcome: ALLOW (Granted by security_officer_alice)
```

### 5.1 Approval Verification Safeguards
1. **Human-Only Authority**: Review submissions validate `is_ai_or_llm == False`. Any actor prefixed with `ai:` or `llm:` is rejected with `403 Forbidden`.
2. **Single-Decision Finality**: Once an approval request is in a terminal state (`APPROVED`, `REJECTED`, or `EXPIRED`), attempting to submit another decision raises `409 Conflict`.
3. **Policy Revision Drift Protection**:
   - Each `ApprovalRequest` binds to the `policy_id` and exact `policy_version` active at the moment of evaluation.
   - If the underlying policy is updated to a newer revision (e.g. from `v1` to `v2`) while the approval is pending or granted, the approval request **automatically expires** (`EXPIRED`), and subsequent execution checks fail closed to `BLOCK`.
4. **Deactivated Policy Protection**: If a policy is deactivated (`enabled=false`) after approval was granted, subsequent authorization checks block execution with `POLICY_DISABLED`.

---

## 6. Decision History Persistence

AMGR guarantees auditability by recording every evaluated decision in `DecisionRepository` backed by `PolicyDecisionModel` (`policy_decisions` table):

- **Separation of Concerns**: Runtime decision records are stored in `policy_decisions`, completely separate from editable policy configurations (`policies`).
- **Trace Correlation**: Every decision is indexed by `trace_id`, enabling end-to-end provenance queries for audit trails and security incident investigations.
- **Audited Fields**: Captures `id`, `trace_id`, `subject`, `action`, `resource`, `decision`, `policy_id`, `matched_rule_id`, `matched_rule_ids`, `matched_policy_ids`, `reason_code`, `reason_message`, `context_data`, and `evaluated_at`.
