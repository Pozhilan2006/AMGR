# AMGR Policy Contract Specification

This document defines the formal contract, declarative schema, and deterministic evaluation semantics for policies within the **Autonomous Multi-Agent Governance Runtime (AMGR)**.

---

## 1. Overview & Architectural Principles

AMGR governs interactions between autonomous AI agents, tools, models, and system resources. Governance policies enforce deterministic boundaries:

- **Deterministic Evaluation**: Given identical request attributes and policy definitions, the engine always produces the exact same decision and machine-readable reason code. Arbitrary script execution (`eval()`, dynamic code blocks, or LLM-based decision overrides) is strictly forbidden.
- **Fail-Closed by Default**: When no policies match, when security attributes are missing, or when policies are deactivated, the system strictly falls back to safe denial (`BLOCK`).
- **Immutability of History**: Historical policy revisions and decision audit records cannot be mutated or overwritten in place.

---

## 2. Declarative Condition AST

Policy rules inspect evaluation context using declarative Condition Abstract Syntax Trees (ASTs).

### 2.1 Attribute Resolution (`resolve_attribute`)

Attribute paths use safe dotted-path identifiers (e.g., `subject.role`, `context.destination_ip`, `action`):
- Regex pattern enforced: `^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*$`
- Dunder traversal (`__class__`, `__globals__`, etc.) is strictly forbidden and returns `INVALID_PATH`.
- Attribute resolution returns a status tuple: `(AttributeResolutionStatus, value)`.
  - `FOUND`: Key exists along the path.
  - `MISSING`: Any key along the path does not exist.
  - `INVALID_PATH`: Malformed syntax or forbidden path.

### 2.2 Atomic Condition (`ConditionRule`)

A `ConditionRule` inspects a single field against a target value using an explicitly supported operator:

```json
{
  "field": "context.risk_score",
  "operator": "LTE",
  "value": 75
}
```

#### Supported Operators (`ConditionOperator`)

| Operator | Category | Operand Requirements | Semantics |
| :--- | :--- | :--- | :--- |
| `EQ` | Equality | Primitive scalar or JSON array/object | Exact equality (`actual == expected`) |
| `NOT_EQ` | Equality | Primitive scalar or JSON array/object | Negated equality (`actual != expected`) |
| `IN` | Membership | Collection (`list`, `tuple`, `set`) | Value membership (`actual in expected`) |
| `NOT_IN` | Membership | Collection (`list`, `tuple`, `set`) | Negated membership (`actual not in expected`) |
| `CONTAINS` | Pattern | String (`str`) | Substring match (`expected in actual`) |
| `STARTS_WITH` | Pattern | String (`str`) | Prefix match (`actual.startswith(expected)`) |
| `ENDS_WITH` | Pattern | String (`str`) | Suffix match (`actual.endswith(expected)`) |
| `GT` | Relational | Numeric (`int`, `float`; strictly not `bool`) | Greater than (`actual > expected`) |
| `GTE` | Relational | Numeric (`int`, `float`; strictly not `bool`) | Greater than or equal (`actual >= expected`) |
| `LT` | Relational | Numeric (`int`, `float`; strictly not `bool`) | Less than (`actual < expected`) |
| `LTE` | Relational | Numeric (`int`, `float`; strictly not `bool`) | Less than or equal (`actual <= expected`) |
| `EXISTS` | Presence | Must be `None` (no value allowed) | True if attribute is `FOUND` |
| `NOT_EXISTS` | Presence | Must be `None` (no value allowed) | True if attribute is `MISSING` or `INVALID_PATH` |

#### Behavior on Missing Attributes
- If an attribute resolution returns `MISSING` or `INVALID_PATH`:
  - `NOT_EXISTS` evaluates to `True`.
  - `EXISTS` evaluates to `False`.
  - **All other operators evaluate to `False`**. Missing attributes never satisfy a positive match.

### 2.3 Compound Condition (`ConditionGroup`)

A `ConditionGroup` combines multiple rules or nested groups using boolean logic:

```json
{
  "all_of": [
    {
      "field": "action",
      "operator": "EQ",
      "value": "execute_code"
    },
    {
      "any_of": [
        {
          "field": "context.environment",
          "operator": "EQ",
          "value": "production"
        },
        {
          "field": "context.privileged",
          "operator": "EQ",
          "value": true
        }
      ]
    }
  ]
}
```

- `all_of`: Logical AND (all children must evaluate to `True`).
- `any_of`: Logical OR (at least one child must evaluate to `True`).
- `none_of`: Logical NOT (none of the children may evaluate to `True`).
- **Nesting Guard**: Maximum condition depth is bounded by `MAX_CONDITION_DEPTH = 5`. Deeper structures are rejected at validation time.

---

## 3. Policy Rule Definition (`RuleDefinition`)

A rule binds a condition AST to an enforcement outcome, priority, and optional target scope filters:

```json
{
  "id": "rule_block_production_sql",
  "name": "Block direct SQL in production",
  "description": "Prevents unauthorized database modifications in prod clusters",
  "condition": {
    "all_of": [
      { "field": "context.environment", "operator": "EQ", "value": "production" },
      { "field": "action", "operator": "EQ", "value": "db_query" }
    ]
  },
  "effect": "BLOCK",
  "priority": 100,
  "enabled": true,
  "target_actions": ["db_query", "db_execute"],
  "target_resources": ["db://prod-primary"]
}
```

### Fields & Validation Rules
- `id`: Non-empty alphanumeric identifier string matching `^[a-zA-Z_][a-zA-Z0-9_-]*$`. Cannot start with dunder `__`.
- `name`: Non-empty human-readable string.
- `description`: Optional documentation note.
- `condition`: Valid `ConditionRule` or `ConditionGroup`.
- `effect`: One of `PolicyEffect`:
  - `ALLOW`: Permitted to execute immediately.
  - `BLOCK`: Denied execution at the runtime boundary.
  - `REQUIRE_APPROVAL`: Suspended pending human operator approval.
- `priority`: Non-negative integer (`ge=0`, default `0`). Higher value indicates higher precedence.
- `enabled`: Boolean (default `true`). Disabled rules never evaluate to `True`.
- `target_actions`: Optional list of strings. If specified, the rule applies only when `request.action` matches.
- `target_resources`: Optional list of strings. If specified, the rule applies only when `request.resource` matches.

---

## 4. Policy Definition & Resolution (`PolicyCreate`, `PolicyRead`)

A policy groups multiple rules under an overarching lifecycle and conflict-resolution strategy.

```json
{
  "id": "pol_data_exfiltration_guard",
  "name": "Data Exfiltration Guard",
  "description": "Monitors outbound network tools and sensitive data exports",
  "priority": 50,
  "resolution_algorithm": "BLOCK_OVERRIDES",
  "default_effect": "BLOCK",
  "enabled": true,
  "rules": [
    {
      "id": "rule_allow_internal",
      "name": "Allow internal webhook",
      "condition": {
        "field": "context.target_host",
        "operator": "ENDS_WITH",
        "value": ".internal.company.com"
      },
      "effect": "ALLOW",
      "priority": 10
    },
    {
      "id": "rule_block_public_export",
      "name": "Block external exports",
      "condition": {
        "field": "context.target_host",
        "operator": "NOT_IN",
        "value": ["api.internal.company.com", "metrics.internal.company.com"]
      },
      "effect": "BLOCK",
      "priority": 50
    }
  ]
}
```

### Rule Conflict Resolution (`RuleResolutionAlgorithm`)
When evaluating multiple matching rules within a single policy:

1. `FIRST_APPLICABLE`: Rules are sorted by `priority` descending (preserving definition order on ties). The highest-priority matching rule dictates the decision.
2. `BLOCK_OVERRIDES` (Default):
   - If any matching rule produces `BLOCK`, the outcome is `BLOCK`.
   - Else if any matching rule produces `REQUIRE_APPROVAL`, the outcome is `REQUIRE_APPROVAL`.
   - Otherwise, `ALLOW`.
3. `ALLOW_OVERRIDES`:
   - If any matching rule produces `ALLOW`, the outcome is `ALLOW`.
   - Else if any matching rule produces `REQUIRE_APPROVAL`, the outcome is `REQUIRE_APPROVAL`.
   - Otherwise, `BLOCK`.

### Safe Fallback
If zero rules match within the policy, or if the policy is marked `enabled: false`, the policy strictly renders `default_effect` (which defaults to `BLOCK`).

---

## 5. Multi-Policy Priority & Conflict Resolution (`evaluate_policies`)

When evaluating an authorization request across multiple policies:

1. **Filtering**: Only enabled policies are evaluated. If no enabled policies exist, returns `default_effect` (BLOCK) with reason code `POLICY_DISABLED` or `DEFAULT_FALLBACK`.
2. **Deterministic Ordering**: Policies are sorted by `priority` descending, breaking ties deterministically by `id` ascending (`sort_policies`).
3. **Priority Tiers**: Policies are grouped into discrete priority tiers.
   - The engine evaluates the highest priority tier first.
   - If one or more policies match in the highest tier, evaluation halts and does not proceed to lower tiers.
4. **Tier Conflict Resolution**:
   - **Single Match**: The matching policy dictates the decision (`RULE_MATCHED`).
   - **Unanimous Agreement**: All matching policies yield the same effect (`RULE_MATCHED`).
   - **Conflicting Outcomes**: If policies within the same priority tier produce differing effects, the conflict is resolved via the configured `conflict_algorithm`:
     - Under `BLOCK_OVERRIDES`, `BLOCK` overrides `REQUIRE_APPROVAL` and `ALLOW`.
     - Under `ALLOW_OVERRIDES`, `ALLOW` overrides `REQUIRE_APPROVAL` and `BLOCK`.
     - Reason code is set to `CONFLICT_RESOLVED`.
5. **Global Fallback**: If zero policies match across all tiers, the engine returns `default_effect` (BLOCK) with `DEFAULT_FALLBACK`.

---

## 6. Persistence & Versioning Semantics

Policy definitions are managed via `PolicyRepository` supporting dual-mode persistence (SQLAlchemy relational database with in-memory fallback):

- **Monotonic Versioning**: Initial creation produces version `1`. Every successful update increments the version monotonically (`v2, v3, ...`).
- **Immutability of Revisions**: Past versions are preserved in `PolicyVersionModel` (`policy_versions` table) and cannot be modified.
- **Audit History**: Every mutation records a `PolicyHistoryEntry` with timestamp, actor attribution, change description, and change type:
  - `CREATED`: Initial policy registration.
  - `UPDATED`: Modification of rules, priority, description, or algorithms.
  - `ACTIVATED`: Toggling `enabled` from `false` to `true`.
  - `DEACTIVATED`: Toggling `enabled` from `true` to `false`.
- **Transactional Safety**: If an update fails validation (e.g., duplicate rule IDs), neither in-memory state nor database records are modified, and no version increment occurs.
