from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.ai_operator.evidence import EvidenceRecord
from app.ai_operator.schemas import OperatorScope
from app.ai_operator.tools.registry import REGISTRY
from app.ai_operator.tools.schemas import ToolCallRequest, ToolResult


class ToolExecutionError(Exception):
    pass


class ToolValidationError(ToolExecutionError):
    pass


class ToolAuthorizationError(ToolExecutionError):
    pass


async def execute_tool_call(tool_call: ToolCallRequest, *, scope: OperatorScope) -> ToolResult:
    tool = REGISTRY.get(tool_call.tool_name)
    if tool is None:
        raise ToolValidationError(f"Unknown tool requested: {tool_call.tool_name}")

    if not tool.read_only:
        raise ToolAuthorizationError(f"Tool {tool_call.tool_name} is not read-only and is not permitted.")

    try:
        args = tool.input_schema.model_validate(tool_call.arguments)
    except ValidationError as exc:
        raise ToolValidationError(f"Invalid arguments for tool {tool_call.tool_name}: {exc}") from exc

    try:
        payload = await tool.handler(**args.model_dump(), scope=scope)
    except TypeError as exc:
        raise ToolExecutionError(f"Execution failed for tool {tool_call.tool_name}: invalid handler signature") from exc
    except Exception as exc:  # pragma: no cover - safety boundary
        raise ToolExecutionError(f"The request for tool {tool_call.tool_name} could not be completed: {exc}") from exc

    def _normalize_evidence(items: Any) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        normalized: list[dict[str, Any]] = []
        for evidence in items:
            if not isinstance(evidence, dict):
                continue
            normalized.append({
                "evidence_type": str(evidence.get("evidence_type", "unknown")),
                "record_id": evidence.get("record_id"),
                "source_service": str(evidence.get("source_service", "amgr")),
                "timestamp": evidence.get("timestamp"),
                "relationship_type": evidence.get("relationship_type"),
                "observation_status": str(evidence.get("observation_status", "observed")),
                "summary": str(evidence.get("summary", "Evidence available.")),
            })
        return normalized

    if payload.get("status") in {"error", "forbidden", "unavailable"}:
        return ToolResult(
            status=str(payload.get("status", "error")),
            message=str(payload.get("message", "The tool could not complete the request.")),
            data=payload.get("data", {}),
            warnings=payload.get("warnings", []),
            evidence=_normalize_evidence(payload.get("evidence", [])),
        )

    return ToolResult(
        status=str(payload.get("status", "success")),
        message=str(payload.get("message", "The tool completed successfully.")),
        data=payload.get("data", payload),
        warnings=payload.get("warnings", []),
        evidence=_normalize_evidence(payload.get("evidence", [])),
    )
