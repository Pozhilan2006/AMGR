from __future__ import annotations

import re

from app.ai_operator.query_schemas import (
    InvestigationEvidence,
    InvestigationIntent,
    InvestigationIntentType,
    InvestigationResponse,
    normalize_identifier,
)
from app.ai_operator.schemas import OperatorScope
from app.ai_operator.tools import (
    explain_decision,
    find_decisions_for_flow,
    list_recent_decisions,
    lookup_component_neighbors,
    lookup_downstream_lineage,
    lookup_external_service_interactions,
    lookup_path,
    lookup_upstream_lineage,
)

_INTENT_PATTERNS = {
    InvestigationIntentType.COMPONENT_LOOKUP: (
        re.compile(r"\b(component|node|service|component_id|component id|what is|who is)\b", re.IGNORECASE),
        re.compile(r"\bmetadata|neighbors|relationships|direct relationships\b", re.IGNORECASE),
    ),
    InvestigationIntentType.UPSTREAM_LINEAGE: (
        re.compile(r"\bupstream|parent|source of|origin of|preceding\b", re.IGNORECASE),
    ),
    InvestigationIntentType.DOWNSTREAM_LINEAGE: (
        re.compile(r"\bdownstream|child|destination of|affected by|after this\b", re.IGNORECASE),
    ),
    InvestigationIntentType.PATH_LOOKUP: (
        re.compile(r"\bpath\b|\broute\b|\bbetween\b.*\band\b", re.IGNORECASE),
    ),
    InvestigationIntentType.EXTERNAL_SERVICE_INTERACTION: (
        re.compile(r"\bexternal service|third[- ]party|api call|outbound|interaction with\b", re.IGNORECASE),
    ),
    InvestigationIntentType.POLICY_DECISION_EXPLANATION: (
        re.compile(r"\bdecision|allow|deny|approve|reject|why was.*denied|why.*blocked\b", re.IGNORECASE),
    ),
    InvestigationIntentType.EVENT_HISTORY_LOOKUP: (
        re.compile(r"\bevent history|history|events|flow id|event id\b", re.IGNORECASE),
    ),
    InvestigationIntentType.AMGR_CAPABILITY_EXPLANATION: (
        re.compile(r"\bwhat can amgr do|capabilities|supported features|platform functionality\b", re.IGNORECASE),
    ),
}


async def _classify_intent(message: str) -> InvestigationIntent:
    lowered = message.lower().strip()
    if not lowered:
        raise ValueError("A user message is required.")

    matched_type: InvestigationIntentType | None = None
    for intent_type, patterns in _INTENT_PATTERNS.items():
        if any(pattern.search(lowered) for pattern in patterns):
            matched_type = intent_type
            break

    if matched_type is None:
        raise ValueError("Unsupported investigation request; the operator only supports bounded component, lineage, path, external-service, decision, and capability questions.")

    component_id = None
    source_component_id = None
    destination_component_id = None
    flow_id = None
    event_id = None
    decision_id = None
    direction = None
    limit = 10
    traversal_depth = 2

    if matched_type in {InvestigationIntentType.COMPONENT_LOOKUP, InvestigationIntentType.UPSTREAM_LINEAGE, InvestigationIntentType.DOWNSTREAM_LINEAGE}:
        match = re.search(r"(?:component|node|service|component id)[\s:]+([A-Za-z0-9_.:/-]+)", message, re.IGNORECASE)
        if match:
            component_id = normalize_identifier(match.group(1))
        elif re.search(r"\bfor\s+([A-Za-z0-9_.:/-]+)\b", message, re.IGNORECASE):
            component_id = normalize_identifier(re.search(r"\bfor\s+([A-Za-z0-9_.:/-]+)\b", message, re.IGNORECASE).group(1))

    if matched_type == InvestigationIntentType.PATH_LOOKUP:
        match = re.search(r"(?:between|from)\s+([A-Za-z0-9_.:/-]+)\s+(?:and|to)\s+([A-Za-z0-9_.:/-]+)", message, re.IGNORECASE)
        if match:
            source_component_id = normalize_identifier(match.group(1))
            destination_component_id = normalize_identifier(match.group(2))

    if matched_type == InvestigationIntentType.EXTERNAL_SERVICE_INTERACTION:
        match = re.search(r"(?:external service|third[- ]party|service|api)\s+(?:named\s+)?([A-Za-z0-9_.:-]+)", message, re.IGNORECASE)
        if match:
            component_id = normalize_identifier(match.group(1))

    if matched_type == InvestigationIntentType.POLICY_DECISION_EXPLANATION:
        match = re.search(r"decision\s+(?:id\s+)?([A-Za-z0-9-]+)", message, re.IGNORECASE)
        if match:
            decision_id = normalize_identifier(match.group(1))
        else:
            match = re.search(r"flow\s+(?:id\s+)?([A-Za-z0-9-]+)", message, re.IGNORECASE)
            if match:
                flow_id = normalize_identifier(match.group(1))

    if matched_type == InvestigationIntentType.EVENT_HISTORY_LOOKUP:
        match = re.search(r"event\s+(?:id\s+)?([A-Za-z0-9-]+)", message, re.IGNORECASE)
        if match:
            event_id = normalize_identifier(match.group(1))
        else:
            match = re.search(r"flow\s+(?:id\s+)?([A-Za-z0-9-]+)", message, re.IGNORECASE)
            if match:
                flow_id = normalize_identifier(match.group(1))

    if "upstream" in lowered:
        direction = "upstream"
    if "downstream" in lowered:
        direction = "downstream"

    return InvestigationIntent(
        intent_type=matched_type,
        component_id=component_id,
        source_component_id=source_component_id,
        destination_component_id=destination_component_id,
        flow_id=flow_id,
        event_id=event_id,
        decision_id=decision_id,
        direction=direction,
        limit=limit,
        traversal_depth=traversal_depth,
        clarification_required=False,
    )


async def route_investigation_request(message: str, scope: OperatorScope) -> InvestigationResponse:
    try:
        intent = await _classify_intent(message)
    except ValueError as exc:
        return InvestigationResponse(
            answer=str(exc),
            intent_type=InvestigationIntentType.AMGR_CAPABILITY_EXPLANATION,
            evidence=[],
            clarification_required=True,
            clarification_question="Please ask a supported AMGR investigation question about a component, lineage, path, external service, decision, or AMGR capability.",
            limitations=["The repository does not implement a broad free-form investigation engine."],
        )

    if intent.intent_type == InvestigationIntentType.COMPONENT_LOOKUP:
        if not intent.component_id:
            return InvestigationResponse(
                answer="I can help with a component lookup, but I need a stable component identifier or a specific component name to check.",
                intent_type=intent.intent_type,
                evidence=[],
                clarification_required=True,
                clarification_question="Which component or service do you want to inspect?",
                limitations=["The current repository does not expose a graph API for authoritative component lookup."],
            )
        result = await lookup_component_neighbors(intent.component_id, scope=scope, limit=intent.limit)
        limitations = ["No authoritative graph API is implemented in this repository for component relationships."]
        evidence = [InvestigationEvidence(reference_type="graph_event", reference_id=result.component_id, summary=result.message or "Component lookup result is unavailable.")]
        if result.unavailable:
            answer = (
                "I could not find an authoritative component relationship record for this component in the current AMGR implementation. "
                "The repository exposes no graph API yet, so the result is limited to the identifier you supplied and the known "
                "lack of graph visibility."
            )
        else:
            answer = f"Component {result.component_id} was requested and the available graph metadata returned {result.neighbors or 'no neighbor'} relationships in scope {result.scope}."
        return InvestigationResponse(answer=answer, intent_type=intent.intent_type, evidence=evidence, clarification_required=False, limitations=limitations)

    if intent.intent_type == InvestigationIntentType.UPSTREAM_LINEAGE:
        if not intent.component_id:
            return InvestigationResponse(
                answer="I can trace upstream lineage, but I need a specific component identifier first.",
                intent_type=intent.intent_type,
                evidence=[],
                clarification_required=True,
                clarification_question="Which component's upstream lineage do you want to inspect?",
                limitations=["No upstream lineage API is implemented in this repository."],
            )
        result = await lookup_upstream_lineage(intent.component_id, scope=scope, limit=intent.limit, traversal_depth=intent.traversal_depth)
        return InvestigationResponse(
            answer=result.message or "Upstream lineage is unavailable because the lineage interface is not implemented in the current repository.",
            intent_type=intent.intent_type,
            evidence=[InvestigationEvidence(reference_type="graph_event", reference_id=result.component_id, summary=result.message or "No upstream lineage available.")],
            clarification_required=False,
            limitations=["Upstream lineage cannot be established without a Member 2 graph or lineage service."],
        )

    if intent.intent_type == InvestigationIntentType.DOWNSTREAM_LINEAGE:
        if not intent.component_id:
            return InvestigationResponse(
                answer="I can trace downstream lineage, but I need a specific component identifier first.",
                intent_type=intent.intent_type,
                evidence=[],
                clarification_required=True,
                clarification_question="Which component's downstream lineage do you want to inspect?",
                limitations=["No downstream lineage API is implemented in this repository."],
            )
        result = await lookup_downstream_lineage(intent.component_id, scope=scope, limit=intent.limit, traversal_depth=intent.traversal_depth)
        return InvestigationResponse(
            answer=result.message or "Downstream lineage is unavailable because the lineage interface is not implemented in the current repository.",
            intent_type=intent.intent_type,
            evidence=[InvestigationEvidence(reference_type="graph_event", reference_id=result.component_id, summary=result.message or "No downstream lineage available.")],
            clarification_required=False,
            limitations=["Downstream lineage cannot be established without a Member 2 graph or lineage service."],
        )

    if intent.intent_type == InvestigationIntentType.PATH_LOOKUP:
        if not intent.source_component_id or not intent.destination_component_id:
            return InvestigationResponse(
                answer="I can look up a known path between two components, but I need both component identifiers.",
                intent_type=intent.intent_type,
                evidence=[],
                clarification_required=True,
                clarification_question="Which source component and destination component should I compare?",
                limitations=["No path lookup API is implemented in this repository."],
            )
        path = await lookup_path(intent.source_component_id, intent.destination_component_id, scope=scope, limit=intent.limit, traversal_depth=intent.traversal_depth)
        return InvestigationResponse(
            answer=(
                "There is no implemented graph path query in the current repository, so no authoritative path can be reported between "
                f"{intent.source_component_id} and {intent.destination_component_id}."
            ),
            intent_type=intent.intent_type,
            evidence=[
                InvestigationEvidence(reference_type="graph_event", reference_id=intent.source_component_id, summary="Source component in requested path."),
                InvestigationEvidence(reference_type="graph_event", reference_id=intent.destination_component_id, summary="Destination component in requested path."),
            ],
            clarification_required=False,
            limitations=["An actual graph traversal service would be needed to confirm a path."],
        )

    if intent.intent_type == InvestigationIntentType.EXTERNAL_SERVICE_INTERACTION:
        service_name = intent.component_id or intent.source_component_id
        if not service_name:
            return InvestigationResponse(
                answer="I can look for recorded external-service interactions, but I need the external service name or identifier.",
                intent_type=intent.intent_type,
                evidence=[],
                clarification_required=True,
                clarification_question="Which external service or API should I look for?",
                limitations=["No external-service interaction API is implemented in this repository."],
            )
        result = await lookup_external_service_interactions(service_name, scope=scope, limit=intent.limit)
        return InvestigationResponse(
            answer=result.message or "No external-service interaction records are available in the current repository implementation.",
            intent_type=intent.intent_type,
            evidence=[InvestigationEvidence(reference_type="graph_event", reference_id=result.service_name, summary=result.message or "No external-service interaction data available.")],
            clarification_required=False,
            limitations=["The repository exposes no actual external-service interaction records yet."],
        )

    if intent.intent_type == InvestigationIntentType.POLICY_DECISION_EXPLANATION:
        if not intent.decision_id and not intent.flow_id:
            return InvestigationResponse(
                answer="I can explain a recorded decision only if you provide a decision ID or a flow ID.",
                intent_type=intent.intent_type,
                evidence=[],
                clarification_required=True,
                clarification_question="What decision ID or flow ID should I check?",
                limitations=["Authorization decision history is not implemented in this repository."],
            )
        decision = await explain_decision(intent.decision_id, scope=scope) if intent.decision_id else await explain_decision(None, scope=scope)
        if not intent.decision_id and intent.flow_id:
            decisions = await find_decisions_for_flow(intent.flow_id, scope=scope, limit=intent.limit)
            decision = decisions[0] if decisions else None
        if decision is None:
            return InvestigationResponse(
                answer="No matching decision record was found within the queried scope.",
                intent_type=intent.intent_type,
                evidence=[],
                clarification_required=False,
                limitations=["No decision API is implemented and no actual decision records are available in this repository."],
            )
        return InvestigationResponse(
            answer=decision.message or "No matching decision record was found within the queried scope.",
            intent_type=intent.intent_type,
            evidence=[InvestigationEvidence(reference_type="decision", reference_id=decision.decision_id or (intent.decision_id or intent.flow_id or "unknown"), summary=decision.message or "Decision record unavailable.")],
            clarification_required=False,
            limitations=["The decision history API is not implemented in this repository, so no actual decision outcome can be explained yet."],
        )

    if intent.intent_type == InvestigationIntentType.EVENT_HISTORY_LOOKUP:
        if not intent.event_id and not intent.flow_id:
            return InvestigationResponse(
                answer="I can retrieve relevant event or decision history only when an event ID or flow ID is supplied.",
                intent_type=intent.intent_type,
                evidence=[],
                clarification_required=True,
                clarification_question="What event ID or flow ID should I look up?",
                limitations=["Event ingestion and history APIs are not implemented in this repository."],
            )
        return InvestigationResponse(
            answer="No event or decision-history records are available in the current repository implementation. The event ingestion and decision-history interfaces remain unimplemented.",
            intent_type=intent.intent_type,
            evidence=[
                InvestigationEvidence(reference_type="graph_event", reference_id=intent.event_id or intent.flow_id or "unknown", summary="No matching event history is available in the current repository."),
            ],
            clarification_required=False,
            limitations=["Member 2 graph/event ingestion and Member 3 authorization history are not implemented."],
        )

    if intent.intent_type == InvestigationIntentType.AMGR_CAPABILITY_EXPLANATION:
        capabilities = [
            "AMGR captures runtime flow data through SDK and event contracts.",
            "The architecture separates graph/lineage, policy evaluation, approvals, and AI operator concerns.",
            "The current repository is a starter implementation with several endpoints intentionally returning 501 Not Implemented.",
            "The operator is designed to explain evidence-based findings, not to perform policy activation or authorization actions.",
        ]
        return InvestigationResponse(
            answer="AMGR is a runtime information-flow control plane that captures event metadata, assembles graph and lineage context, evaluates policy decisions, and surfaces explanation through the AI Security Operator. In the current repository, the implemented functionality is limited to the API skeleton, typed schemas, and the operator's read-only request/response pipeline. " + " ".join(capabilities),
            intent_type=intent.intent_type,
            evidence=[],
            clarification_required=False,
            limitations=["The repository does not yet implement the full graph, lineage, event, or policy history surfaces described in the architecture."],
        )

    return InvestigationResponse(
        answer="This investigation request is unsupported in the current AMGR implementation.",
        intent_type=InvestigationIntentType.AMGR_CAPABILITY_EXPLANATION,
        evidence=[],
        clarification_required=True,
        clarification_question="Please narrow your request to a supported component, lineage, path, external-service, decision, or capability question.",
        limitations=["Unsupported questions require interfaces not present in the repository."],
    )
