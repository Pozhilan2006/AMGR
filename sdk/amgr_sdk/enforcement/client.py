from datetime import datetime, timezone
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from amgr_sdk.models.events import ComponentRef


class AuthorizationRequest(BaseModel):
    request_id: UUID = Field(default_factory=uuid4)
    flow_id: UUID
    correlation_id: UUID | None = None
    subject: ComponentRef
    action: str
    resource: ComponentRef
    data_labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuthorizationDecision(BaseModel):
    decision_id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    outcome: Literal["allow", "deny", "review"]
    reason: str
    policy_ids: list[UUID] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AuthorizationClient(Protocol):
    def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        """Ask an implementation to evaluate an operation before it is performed."""