"""AMGR FastAPI backend application entrypoint.

Initializes FastAPI service, mounts authorization & governance API routers,
and provides application factory with dependency injection.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.authorization import (
    get_approval_service,
    get_authorization_service,
    get_policy_repository,
    router as authorization_router,
)
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.services.approval_service import ApprovalService
from backend.app.services.authorization_service import AuthorizationService


def create_app(
    policy_repo: PolicyRepository | None = None,
    approval_service: ApprovalService | None = None,
    auth_service: AuthorizationService | None = None,
) -> FastAPI:
    """Creates and configures the FastAPI application instance.

    Allows injecting custom or mock repository and service instances for tests.
    """
    app = FastAPI(
        title="AMGR Control Plane",
        description="Autonomous Multi-Agent Governance Runtime Control Plane & Policy Enforcement API",
        version="0.1.0",
    )

    # Configure CORS for local development & dashboard integration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Dependency overrides if custom instances were provided
    if policy_repo is not None:
        app.dependency_overrides[get_policy_repository] = lambda: policy_repo
    if approval_service is not None:
        app.dependency_overrides[get_approval_service] = lambda: approval_service
    if auth_service is not None:
        app.dependency_overrides[get_authorization_service] = lambda: auth_service

    # Health check endpoints
    @app.get("/health", tags=["system"])
    @app.get("/api/v1/health", tags=["system"])
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    # Include authorization router under configured prefix and at root for flexibility
    api_prefix = os.environ.get("API_V1_PREFIX", "/api/v1").rstrip("/")
    app.include_router(authorization_router, prefix=api_prefix)
    if api_prefix != "":
        # Also mount router without prefix for direct route compatibility
        app.include_router(authorization_router)

    return app


# Default application instance
app = create_app()
