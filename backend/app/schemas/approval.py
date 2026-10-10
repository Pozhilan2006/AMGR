from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Approval(BaseModel):
    approval_id: UUID = Field(default_factory=uuid4)
    decision_id: UUID
    status: Literal["pending", "approved", "rejected"] = "pending"
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))