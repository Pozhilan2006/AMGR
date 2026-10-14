"""HTTP API package for AMGR.

Exports authorization router, policy lifecycle router, and governance service dependencies.
"""

from backend.app.api.authorization import (
    get_approval_service,
    get_authorization_service,
    get_policy_repository,
    reset_services,
    router as authorization_router,
)
from backend.app.api.routes.policies import (
    get_policy_service,
    reset_policy_service,
    router as policies_router,
)

__all__ = [
    "authorization_router",
    "get_approval_service",
    "get_authorization_service",
    "get_policy_repository",
    "get_policy_service",
    "policies_router",
    "reset_policy_service",
    "reset_services",
]