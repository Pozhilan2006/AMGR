from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class ProviderMessage:
    role: Literal["system", "user"]
    content: str


@dataclass(frozen=True)
class ProviderResponse:
    content: str


class AIProvider(Protocol):
    async def complete(self, messages: list[ProviderMessage]) -> ProviderResponse: ...

    async def close(self) -> None: ...


class ProviderConfigurationError(Exception):
    """The configured provider cannot be initialized safely."""


class ProviderUnavailableError(Exception):
    """The configured provider could not complete a request."""


class UnusableProviderResponse(Exception):
    """The provider response could not be parsed into the public contract."""