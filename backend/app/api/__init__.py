"""HTTP API package for AMGR.

Exports authorization router and governance service dependencies.
"""

from backend.app.api.authorization import (
    get_approval_service,
    get_authorization_service,
    get_policy_repository,
    reset_services,
    router as authorization_router,
)

__all__ = [
    "authorization_router",
    "get_approval_service",
    "get_authorization_service",
    "get_policy_repository",
    "reset_services",
]