from app.ai_operator.providers.base import (
    ProviderConfigurationError,
    ProviderMessage,
    ProviderResponse,
    ProviderUnavailableError,
)
from app.core.config import Settings


class OpenAIProvider:
    def __init__(self, settings: Settings) -> None:
        api_key = settings.ai_operator_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            raise ProviderConfigurationError(
                "AI_OPERATOR_API_KEY is required when the AI operator is used."
            )

        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ProviderConfigurationError(
                "The OpenAI provider dependency is not installed."
            ) from exc

        self._client = AsyncOpenAI(
            api_key=api_key.get_secret_value(),
            base_url=settings.ai_operator_base_url or None,
            timeout=settings.ai_operator_timeout_seconds,
        )
        self._model = settings.ai_operator_model
        self._max_response_tokens = settings.ai_operator_max_response_tokens
        self._temperature = settings.ai_operator_temperature

    async def complete(self, messages: list[ProviderMessage]) -> ProviderResponse:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": message.role, "content": message.content}
                    for message in messages
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=self._max_response_tokens,
                temperature=self._temperature,
            )
        except Exception as exc:
            raise ProviderUnavailableError(
                "The configured AI provider could not complete the request."
            ) from exc

        if not response.choices or not response.choices[0].message.content:
            raise ProviderUnavailableError(
                "The configured AI provider returned an empty response."
            )
        return ProviderResponse(content=response.choices[0].message.content)

    async def close(self) -> None:
        await self._client.close()