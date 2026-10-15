"""Governance approvals API routes module.

Canonical approval endpoints are defined and served by
`backend.app.api.authorization` via `ApprovalService` and `ApprovalRepository`.
This module maintains `router` for backwards compatibility with central imports.
"""

from fastapi import APIRouter

router = APIRouter()