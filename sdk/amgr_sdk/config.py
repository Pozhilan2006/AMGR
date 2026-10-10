import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AMGRConfig:
    api_base_url: str = "http://localhost:8000/api/v1"
    timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "AMGRConfig":
        return cls(
            api_base_url=os.getenv("AMGR_API_BASE_URL", cls.api_base_url),
            timeout_seconds=float(os.getenv("AMGR_TIMEOUT_SECONDS", cls.timeout_seconds)),
        )