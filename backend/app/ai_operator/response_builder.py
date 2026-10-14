from __future__ import annotations

from app.ai_operator.evidence import EvidenceRecord
from app.ai_operator.tools.schemas import ToolResult


def build_tool_response(question: str, tool_result: ToolResult) -> dict[str, object]:
    if tool_result.status == "empty":
        answer = f"I searched the authorized AMGR records for your question and found no matching records for the requested scope. {tool_result.message}"
    elif tool_result.status in {"error", "forbidden", "unavailable"}:
        answer = f"The requested investigation could not complete in the current AMGR implementation. {tool_result.message}"
    else:
        answer = tool_result.message

    return {
        "answer": answer,
        "evidence": [
            {
                "evidence_type": evidence.evidence_type,
                "record_id": evidence.record_id,
                "source_service": evidence.source_service,
                "timestamp": evidence.timestamp.isoformat() if evidence.timestamp else None,
                "relationship_type": evidence.relationship_type,
                "observation_status": evidence.observation_status,
                "summary": evidence.summary,
            }
            for evidence in tool_result.evidence
        ],
        "limitations": tool_result.warnings or ["Only the available evidence from the current repository implementation was used."],
        "tool_execution_summary": {
            "tool_name": tool_result.data.get("tool_name"),
            "status": tool_result.status,
            "warnings": tool_result.warnings,
        },
    }


def build_investigation_summary(question: str, investigation_answer: str, evidence: list[EvidenceRecord], limitations: list[str] | None = None) -> dict[str, object]:
    return {
        "answer": investigation_answer,
        "evidence": [
            {
                "evidence_type": record.evidence_type,
                "record_id": record.record_id,
                "source_service": record.source_service,
                "timestamp": record.timestamp.isoformat() if record.timestamp else None,
                "relationship_type": record.relationship_type,
                "observation_status": record.observation_status,
                "summary": record.summary,
            }
            for record in evidence
        ],
        "limitations": limitations or ["The current AMGR repository does not expose authoritative graph, lineage, or policy history records."],
    }
