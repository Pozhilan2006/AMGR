# AMGR architecture

## Intent

AMGR is an information-flow control plane for AI systems. It records runtime interactions among software components, models, tools, operators, and external systems, then evaluates those events against deterministic security policies.

## Runtime flow

1. Instrumented application emits events with SDK capture helpers.
2. The Python SDK attaches correlation metadata, flow IDs, and metadata labels.
3. Event ingestion accepts a typed payload through the backend API.
4. The graph and lineage services map events into a connected runtime view.
5. Policy evaluation checks conditions and precedence.
6. An enforcement decision is returned to the caller or a workflow step.
7. The dashboard and AI Security Operator consume the decision history and explain the reasoning.

## Boundary responsibilities

- SDK capture boundary: component metadata, context propagation, event emission, and pre-operation enforcement.
- Backend ingestion boundary: API validation, storage, and event-to-graph mapping.
- Policy boundary: policy schemas, rule evaluation, precedence, authorization decisions, and decision history.
- UI boundary: dashboard visualization, approvals, and operator surfaces.

## Enforcement requirement

For runtime blocking to be effective, the enforcement hook must be placed at a protected operation boundary before the action is executed. If the guard is absent or placed too late, the system can still observe events but cannot reliably stop unauthorized execution.

## Out-of-scope for the starter

The starter architecture explicitly avoids implementing complete business logic for:

- live graph traversal
- complex lineage resolution
- full policy evaluation engines
- approval workflow execution
- AI provider orchestration
- production-grade authentication and secrets management

Instead, the code establishes safe, typed contracts and service boundaries to support team parallel development.
