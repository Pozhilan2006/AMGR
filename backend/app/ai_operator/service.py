import json
from collections.abc import AsyncIterator
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.ai_operator.prompts import scoped_system_message
from app.ai_operator.providers.base import (
    AIProvider,
    ProviderConfigurationError,
    ProviderMessage,
    UnusableProviderResponse,
)
from app.ai_operator.providers.openai_provider import OpenAIProvider
from app.ai_operator.schemas import (
    OperatorChatRequest,
    OperatorChatResponse,
    OperatorModelResponse,
    OperatorScope,
)
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
        provider = self._get_provider()
        messages = [
            ProviderMessage(role="system", content=scoped_system_message(scope)),
            ProviderMessage(role="user", content=request.message),
        ]

        raw_response = (await provider.complete(messages)).content
        try:
            model_response = OperatorModelResponse.model_validate(
                json.loads(raw_response)
            )
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise UnusableProviderResponse from exc

        return OperatorChatResponse(
            conversation_id=conversation_id,
            assistant_message=model_response.assistant_message,
            response_type=model_response.response_type,
            references=None,
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