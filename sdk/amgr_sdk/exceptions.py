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
