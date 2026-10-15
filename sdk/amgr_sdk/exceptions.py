"""AMGR SDK Exception hierarchy."""

from __future__ import annotations


class AMGRError(Exception):
    """Base exception for all AMGR SDK errors."""


class ConfigurationError(AMGRError):
    """Raised when configuration parameters or environment variables are invalid."""


class CaptureError(AMGRError):
    """Raised when event capture, serialization, or instrumentation fails."""


class IntegrationError(AMGRError):
    """Raised when an optional third-party client or integration library is unavailable."""


class EnforcementError(AMGRError):
    """Base exception for runtime authorization and policy enforcement failures."""


class EnforcementConnectionError(EnforcementError):
    """Raised when communication with the authorization service fails, times out,
    or returns an HTTP 5xx server error."""


class AuthorizationBlockedError(EnforcementError, PermissionError):
    """Raised when an operation is blocked by a deterministic policy decision."""

    def __init__(self, message: str, decision: object | None = None) -> None:
        super().__init__(message)
        self.decision = decision


class AuthorizationPendingApprovalError(EnforcementError):
    """Raised when an operation is suspended pending human review and approval."""

    def __init__(
        self,
        message: str,
        approval_id: str | None = None,
        decision: object | None = None,
    ) -> None:
        super().__init__(message)
        self.approval_id = approval_id
        self.decision = decision
