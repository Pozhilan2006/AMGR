from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class Policy(BaseModel):
    policy_id: UUID
    name: str
    description: str | None = None
    enabled: bool = False
    effect: Literal["allow", "deny", "require_approval"]
    conditions: dict[str, Any] = Field(default_factory=dict)