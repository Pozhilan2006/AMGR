import json
from collections.abc import AsyncIterator
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.ai_operator.intent_router import route_investigation_request
from app.ai_operator.prompts import scoped_system_message
from app.ai_operator.providers.base import (
    AIProvider,
    ProviderConfigurationError,
    ProviderMessage,
    UnusableProviderResponse,
)
from app.ai_operator.providers.openai_provider import OpenAIProvider
from app.ai_operator.response_builder import build_investigation_summary, build_tool_response
from app.ai_operator.schemas import (
    OperatorChatRequest,
    OperatorChatResponse,
    OperatorModelResponse,
    OperatorReference,
    OperatorScope,
)
from app.ai_operator.tool_executor import (
    ToolAuthorizationError,
    ToolExecutionError,
    ToolValidationError,
    execute_tool_call,
)
from app.ai_operator.tools.schemas import ToolCallEnvelope
from app.core.config import Settings, get_settings


class ConversationManager(Protocol):
    def resolve_id(self, conversation_id: UUID | None) -> UUID: ...


class StatelessConversationManager:
    """Provides correlation IDs without retaining conversation messages."""

    def resolve_id(self, conversation_id: UUID | None) -> UUID:
        return conversation_id or uuid4()


class OperatorService:
    def __init__(
        self,
        settings: Settings,
        provider: AIProvider | None = None,
        conversation_manager: ConversationManager | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._conversation_manager = conversation_manager or StatelessConversationManager()

    async def respond(
        self, request: OperatorChatRequest, scope: OperatorScope
    ) -> OperatorChatResponse:
        conversation_id = self._conversation_manager.resolve_id(request.conversation_id)

        investigation = await route_investigation_request(request.message, scope)
        if investigation.clarification_required:
            return OperatorChatResponse(
                conversation_id=conversation_id,
                assistant_message=investigation.answer,
                response_type="clarification",
                references=None,
                proposed_actions=None,
            )

        provider = self._get_provider()
        messages = [
            ProviderMessage(role="system", content=scoped_system_message(scope)),
            ProviderMessage(
                role="user",
                content=(
                    f"User question: {request.message}\n\n"
                    f"Structured investigation result:\n{json.dumps(investigation.model_dump(mode='json'))}"
                ),
            ),
        ]

        raw_response = (await provider.complete(messages)).content
        try:
            model_response = OperatorModelResponse.model_validate(
                json.loads(raw_response)
            )
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise UnusableProviderResponse from exc

        try:
            tool_payload = ToolCallEnvelope.model_validate(json.loads(raw_response))
        except Exception:
            tool_payload = None

        if tool_payload and tool_payload.tool_call:
            try:
                tool_result = await execute_tool_call(tool_payload.tool_call, scope=scope)
                response_summary = build_tool_response(request.message, tool_result)
                references = [
                    OperatorReference(
                        reference_type=(
                            "graph_event"
                            if evidence.get("evidence_type") == "component" or evidence.get("evidence_type") == "event" or evidence.get("evidence_type") == "flow" or evidence.get("evidence_type") == "lineage"
                            else "decision"
                            if evidence.get("evidence_type") == "decision"
                            else "policy"
                        ),
                        reference_id=str(evidence.get("record_id") or "unknown"),
                    )
                    for evidence in response_summary["evidence"]
                ]
                return OperatorChatResponse(
                    conversation_id=conversation_id,
                    assistant_message=response_summary["answer"],
                    response_type="answer",
                    references=references or None,
                    proposed_actions=None,
                )
            except (ToolValidationError, ToolAuthorizationError, ToolExecutionError):
                return OperatorChatResponse(
                    conversation_id=conversation_id,
                    assistant_message="The model requested a read-only tool that could not be validated or executed in the current AMGR context.",
                    response_type="clarification",
                    references=None,
                    proposed_actions=None,
                )

        references = [
            OperatorReference(
                reference_type="graph_event" if evidence.reference_type == "graph_event" else "decision" if evidence.reference_type == "decision" else "policy",
                reference_id=evidence.reference_id,
            )
            for evidence in investigation.evidence
        ]

        return OperatorChatResponse(
            conversation_id=conversation_id,
            assistant_message=model_response.assistant_message,
            response_type=model_response.response_type,
            references=references or None,
            proposed_actions=model_response.proposed_actions,
        )

    async def aclose(self) -> None:
        if self._provider is not None:
            await self._provider.close()

    def _get_provider(self) -> AIProvider:
        if self._provider is not None:
            return self._provider

        provider_name = self._settings.ai_operator_provider.strip().lower()
        if provider_name != "openai":
            raise ProviderConfigurationError(
                f"Unsupported AI_OPERATOR_PROVIDER: {provider_name}."
            )

        self._provider = OpenAIProvider(self._settings)
        return self._provider


async def get_operator_service() -> AsyncIterator[OperatorService]:
    service = OperatorService(settings=get_settings())
    try:
        yield service
    finally:
        await service.aclose()