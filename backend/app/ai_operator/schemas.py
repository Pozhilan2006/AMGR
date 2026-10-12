from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OperatorScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    application_id: str = Field(min_length=1, max_length=200)
    environment: str = Field(min_length=1, max_length=100)


class OperatorChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=12000)
    conversation_id: UUID | None = None
    scope: OperatorScope | None = None


class OperatorIdentity(BaseModel):
    """Trusted identity shape supplied by authentication middleware."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1)
    authorized_scopes: list[OperatorScope] = Field(min_length=1)


class OperatorReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_type: Literal["graph_event", "policy", "decision"]
    reference_id: str = Field(min_length=1, max_length=200)


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    review_required: Literal[True] = True


class OperatorModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assistant_message: str = Field(min_length=1, max_length=12000)
    response_type: Literal["answer", "clarification", "proposal"]
    references: list[OperatorReference] | None = None
    proposed_actions: list[ProposedAction] | None = None


class OperatorChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    assistant_message: str
    response_type: Literal["answer", "clarification", "proposal", "error"]
    references: list[OperatorReference] | None = None
    proposed_actions: list[ProposedAction] | None = None