"""AMGR FastAPI backend application entrypoint.

Initializes FastAPI service, mounts authorization & governance API routers,
and provides application factory with dependency injection.
"""

from __future__ import annotations

import importlib.abc
import importlib.util
import os
import sys
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


class _AppAliasLoader(importlib.abc.Loader):
    """Loads and returns the corresponding backend.app.* module."""

    def __init__(self, name: str) -> None:
        self.name = name

    def create_module(self, spec: Any) -> Any:
        import importlib
        return importlib.import_module(self.name)

    def exec_module(self, module: Any) -> None:
        pass


class _AppAliasFinder(importlib.abc.MetaPathFinder):
    """Resolves 'app.*' imports directly to 'backend.app.*' to prevent dual-loading of models."""

    def find_spec(self, fullname: str, path: Any, target: Any = None) -> Any:
        if fullname == "app" or fullname.startswith("app."):
            backend_fullname = "backend." + fullname
            try:
                backend_spec = importlib.util.find_spec(backend_fullname)
                if backend_spec is not None:
                    return importlib.util.spec_from_loader(
                        fullname,
                        _AppAliasLoader(backend_fullname),
                        origin=backend_spec.origin,
                    )
            except Exception:
                pass
        return None


if not any(isinstance(f, _AppAliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _AppAliasFinder())

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import TextClause


@compiles(TextClause, "sqlite")
def _compile_text_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    """Strips PostgreSQL-specific '::jsonb' type casts when executing DDL on SQLite."""
    text_val = str(getattr(element, "text", ""))
    return text_val.replace("::jsonb", "")

from backend.app.api.authorization import (
    get_approval_service,
    get_authorization_service,
    get_decision_repository,
    get_policy_repository,
    router as authorization_router,
)
from backend.app.api.routes.policies import (
    get_policy_service,
    router as policies_router,
)
from backend.app.repositories.decision_repository import DecisionRepository
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.services.approval_service import ApprovalService
from backend.app.services.authorization_service import AuthorizationService
from backend.app.services.policy_service import PolicyService

# Placeholder to satisfy circular import from backend/app/__init__.py during startup
app: FastAPI | None = None


def create_app(
    policy_repo: PolicyRepository | None = None,
    approval_service: ApprovalService | None = None,
    auth_service: AuthorizationService | None = None,
    policy_service: PolicyService | None = None,
    decision_repo: DecisionRepository | None = None,
) -> FastAPI:
    """Creates and configures the FastAPI application instance.

    Allows injecting custom or mock repository and service instances for tests.
    """
    app_instance = FastAPI(
        title="AMGR Control Plane",
        description="Autonomous Multi-Agent Governance Runtime Control Plane & Policy Enforcement API",
        version="0.1.0",
    )

    # Configure CORS for local development & dashboard integration
    app_instance.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Dependency overrides if custom instances were provided
    if policy_repo is not None:
        app_instance.dependency_overrides[get_policy_repository] = lambda: policy_repo
    if approval_service is not None:
        app_instance.dependency_overrides[get_approval_service] = lambda: approval_service
    if auth_service is not None:
        app_instance.dependency_overrides[get_authorization_service] = lambda: auth_service
    if policy_service is not None:
        app_instance.dependency_overrides[get_policy_service] = lambda: policy_service
    if decision_repo is not None:
        app_instance.dependency_overrides[get_decision_repository] = lambda: decision_repo

    # Health check endpoints
    @app_instance.get("/health", tags=["system"])
    @app_instance.get("/api/v1/health", tags=["system"])
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    # Include authorization router under configured prefix and at root for flexibility
    api_prefix = os.environ.get("API_V1_PREFIX", "/api/v1").rstrip("/")
    app_instance.include_router(authorization_router, prefix=api_prefix)
    if api_prefix != "":
        # Also mount router without prefix for direct route compatibility
        app_instance.include_router(authorization_router)

    # Mount central API router under configured prefix (with strict two-way module resolution)
    try:
        from backend.app.api.router import api_router
    except ImportError:
        from app.api.router import api_router

    app_instance.include_router(api_router, prefix=api_prefix)

    # Also mount policies router without prefix for direct root route compatibility
    if api_prefix != "":
        app_instance.include_router(policies_router, prefix="/policies")

    return app_instance


# Default application instance
app = create_app()

# Update package-level attribute if app was imported during initialization
if "app" in sys.modules and hasattr(sys.modules["app"], "app"):
    sys.modules["app"].app = app
