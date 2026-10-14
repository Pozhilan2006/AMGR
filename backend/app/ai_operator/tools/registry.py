from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.ai_operator.query_schemas import InvestigationIntentType
from app.ai_operator.tools.schemas import (
    ComponentDetailsArgs,
    ComponentPathsArgs,
    DecisionHistoryArgs,
    FlowLineageArgs,
    PolicyDecisionArgs,
    PolicyDetailsArgs,
)


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    input_schema: type
    handler: Callable[..., Awaitable[dict[str, Any]]]
    read_only: bool = True
    max_results: int = 10
    max_traversal_depth: int = 4
    required_intent: InvestigationIntentType | None = None


REGISTRY: dict[str, RegisteredTool] = {}


def register_tool(tool: RegisteredTool) -> None:
    REGISTRY[tool.name] = tool


async def get_component_details(*, component_id: str, limit: int = 10, scope: Any) -> dict[str, Any]:
    from app.ai_operator.tools.graph_queries import lookup_component_neighbors

    result = await lookup_component_neighbors(component_id, scope=scope, limit=limit)
    return {
        "status": "unavailable" if result.unavailable else "success",
        "message": result.message or "Component details are unavailable.",
        "component_id": result.component_id,
        "neighbors": result.neighbors,
        "metadata": result.metadata,
        "scope": result.scope,
        "evidence": [{
            "evidence_type": "component",
            "record_id": result.component_id,
            "source_service": "graph_service",
            "summary": result.message or "No component detail record available.",
            "observation_status": "unavailable" if result.unavailable else "observed",
        }],
    }


async def get_flow_lineage(*, component_id: str, direction: str, limit: int = 10, traversal_depth: int = 2, scope: Any) -> dict[str, Any]:
    if direction == "upstream":
        from app.ai_operator.tools.lineage_queries import lookup_upstream_lineage
        result = await lookup_upstream_lineage(component_id, scope=scope, limit=limit, traversal_depth=traversal_depth)
    else:
        from app.ai_operator.tools.lineage_queries import lookup_downstream_lineage
        result = await lookup_downstream_lineage(component_id, scope=scope, limit=limit, traversal_depth=traversal_depth)

    return {
        "status": "unavailable" if result.unavailable else "success",
        "message": result.message or "No lineage record available.",
        "component_id": result.component_id,
        "direction": result.direction,
        "lineage": result.lineage,
        "scope": result.scope,
        "evidence": [{
            "evidence_type": "lineage",
            "record_id": result.component_id,
            "source_service": "lineage_service",
            "summary": result.message or "No lineage record available.",
            "observation_status": "unavailable" if result.unavailable else "incomplete",
            "relationship_type": result.direction,
        }],
    }


async def get_component_paths(*, source_component_id: str, destination_component_id: str, limit: int = 10, traversal_depth: int = 2, scope: Any) -> dict[str, Any]:
    from app.ai_operator.tools.graph_queries import lookup_path

    path = await lookup_path(source_component_id, destination_component_id, scope=scope, limit=limit, traversal_depth=traversal_depth)
    return {
        "status": "success",
        "message": "No implemented path record is available in the current repository.",
        "path": path,
        "source_component_id": source_component_id,
        "destination_component_id": destination_component_id,
        "scope": f"{scope.application_id}:{scope.environment}",
        "evidence": [{
            "evidence_type": "flow",
            "record_id": source_component_id,
            "source_service": "graph_service",
            "summary": "No graph path was available from the implemented repository interfaces.",
            "observation_status": "unavailable",
            "relationship_type": "path",
        }],
    }


async def get_policy_decision(*, decision_id: str | None = None, flow_id: str | None = None, scope: Any) -> dict[str, Any]:
    from app.ai_operator.tools.decision_queries import explain_decision, find_decisions_for_flow

    if decision_id:
        result = await explain_decision(decision_id, scope=scope)
        return {
            "status": "unavailable" if result.unavailable else "success",
            "message": result.message or "No matching decision record found.",
            "decision_id": result.decision_id,
            "outcome": result.outcome,
            "reason": result.reason,
            "policy_ids": result.policy_ids,
            "evaluated_at": result.evaluated_at,
            "scope": result.scope,
            "evidence": [{
                "evidence_type": "decision",
                "record_id": result.decision_id or decision_id,
                "source_service": "authorization_service",
                "summary": result.message or "No decision record is available.",
                "observation_status": "unavailable" if result.unavailable else "observed",
            }],
        }

    if flow_id:
        results = await find_decisions_for_flow(flow_id, scope=scope, limit=10)
        if not results:
            return {
                "status": "empty",
                "message": "No decision records were returned for the queried flow.",
                "flow_id": flow_id,
                "scope": f"{scope.application_id}:{scope.environment}",
                "evidence": [],
            }
        decision = results[0]
        return {
            "status": "unavailable" if decision.unavailable else "success",
            "message": decision.message or "No matching decision record found.",
            "decision_id": decision.decision_id,
            "outcome": decision.outcome,
            "reason": decision.reason,
            "policy_ids": decision.policy_ids,
            "evaluated_at": decision.evaluated_at,
            "scope": decision.scope,
            "evidence": [{
                "evidence_type": "decision",
                "record_id": decision.decision_id or flow_id,
                "source_service": "authorization_service",
                "summary": decision.message or "No decision record is available.",
                "observation_status": "unavailable" if decision.unavailable else "observed",
            }],
        }

    return {
        "status": "error",
        "message": "A decision_id or flow_id is required to retrieve a policy decision.",
        "evidence": [],
    }


async def get_policy_details(*, policy_id: str, scope: Any) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "message": "The policy lifecycle and policy-detail API are not implemented in this repository.",
        "policy_id": policy_id,
        "scope": f"{scope.application_id}:{scope.environment}",
        "evidence": [{
            "evidence_type": "policy",
            "record_id": policy_id,
            "source_service": "policies_service",
            "summary": "No policy detail record is available from the current repository implementation.",
            "observation_status": "unavailable",
        }],
    }


async def get_decision_history(*, flow_id: str | None = None, limit: int = 10, scope: Any) -> dict[str, Any]:
    from app.ai_operator.tools.decision_queries import find_decisions_for_flow, list_recent_decisions

    if flow_id:
        decisions = await find_decisions_for_flow(flow_id, scope=scope, limit=limit)
        if not decisions:
            return {
                "status": "empty",
                "message": "No matching decision history records were returned for the requested flow.",
                "flow_id": flow_id,
                "scope": f"{scope.application_id}:{scope.environment}",
                "data": {"records": []},
                "evidence": [],
            }
        return {
            "status": "unavailable" if decisions[0].unavailable else "success",
            "message": decisions[0].message or "No decision-history records were available.",
            "data": {"records": [decisions[0].model_dump(mode="json")]},
            "scope": f"{scope.application_id}:{scope.environment}",
            "evidence": [{
                "evidence_type": "decision",
                "record_id": decisions[0].decision_id or flow_id,
                "source_service": "authorization_service",
                "summary": decisions[0].message or "No decision-history record available.",
                "observation_status": "unavailable" if decisions[0].unavailable else "observed",
            }],
        }

    decisions = await list_recent_decisions(scope=scope, limit=limit)
    return {
        "status": "unavailable" if decisions and decisions[0].unavailable else "success",
        "message": decisions[0].message if decisions else "No recent decisions are available.",
        "data": {"records": [d.model_dump(mode="json") for d in decisions]},
        "scope": f"{scope.application_id}:{scope.environment}",
        "evidence": [{
            "evidence_type": "decision",
            "record_id": decisions[0].decision_id or "recent",
            "source_service": "authorization_service",
            "summary": decisions[0].message if decisions else "No recent decision records are available.",
            "observation_status": "unavailable" if decisions and decisions[0].unavailable else "incomplete",
        }] if decisions else [],
    }


register_tool(RegisteredTool(
    name="get_component_details",
    description="Retrieve the available details and direct relationships for a known component in the authorized scope.",
    input_schema=ComponentDetailsArgs,
    handler=get_component_details,
    max_results=10,
    required_intent=InvestigationIntentType.COMPONENT_LOOKUP,
))
register_tool(RegisteredTool(
    name="get_flow_lineage",
    description="Retrieve supported upstream or downstream lineage for a component in the authorized scope.",
    input_schema=FlowLineageArgs,
    handler=get_flow_lineage,
    max_results=10,
    max_traversal_depth=8,
    required_intent=InvestigationIntentType.UPSTREAM_LINEAGE,
))
register_tool(RegisteredTool(
    name="get_component_paths",
    description="Retrieve the known path between two components in the authorized scope.",
    input_schema=ComponentPathsArgs,
    handler=get_component_paths,
    max_results=10,
    max_traversal_depth=8,
    required_intent=InvestigationIntentType.PATH_LOOKUP,
))
register_tool(RegisteredTool(
    name="get_policy_decision",
    description="Retrieve a recorded authorization decision or decision history tied to a decision ID or flow ID.",
    input_schema=PolicyDecisionArgs,
    handler=get_policy_decision,
    max_results=10,
    required_intent=InvestigationIntentType.POLICY_DECISION_EXPLANATION,
))
register_tool(RegisteredTool(
    name="get_policy_details",
    description="Retrieve details for a referenced policy if the policy service exposes them in the authorized scope.",
    input_schema=PolicyDetailsArgs,
    handler=get_policy_details,
    max_results=10,
    required_intent=InvestigationIntentType.POLICY_DECISION_EXPLANATION,
))
register_tool(RegisteredTool(
    name="get_decision_history",
    description="Retrieve relevant recent decision-history records for a flow or the current authorized scope.",
    input_schema=DecisionHistoryArgs,
    handler=get_decision_history,
    max_results=10,
    required_intent=InvestigationIntentType.EVENT_HISTORY_LOOKUP,
))

__all__ = [
    "REGISTRY",
    "RegisteredTool",
    "get_component_details",
    "get_component_paths",
    "get_decision_history",
    "get_flow_lineage",
    "get_policy_decision",
    "get_policy_details",
    "register_tool",
]
