"""Public AMGR SDK interfaces."""

from amgr_sdk.capture.decorators import capture, capture_span
from amgr_sdk.capture.recorder import EventRecorder
from amgr_sdk.client import AMGRClient, get_default_client, set_default_client
from amgr_sdk.config import AMGRConfig
from amgr_sdk.context import FlowContext, current_flow_context, flow_context
from amgr_sdk.exceptions import AMGRError, CaptureError, ConfigurationError
from amgr_sdk.models.events import (
    DISALLOWED_METADATA_KEYS,
    STANDARD_COMPONENT_TYPES,
    STANDARD_EVENT_STATUSES,
    ComponentRef,
    FlowEvent,
    sanitize_metadata,
)
from amgr_sdk.transport.base import (
    BatchTransportError,
    Transport,
    TransportConnectionError,
    TransportError,
)
from amgr_sdk.transport.http import HttpTransport

__all__ = [
    "AMGRClient",
    "AMGRConfig",
    "AMGRError",
    "BatchTransportError",
    "CaptureError",
    "ComponentRef",
    "ConfigurationError",
    "DISALLOWED_METADATA_KEYS",
    "EventRecorder",
    "FlowContext",
    "FlowEvent",
    "HttpTransport",
    "STANDARD_COMPONENT_TYPES",
    "STANDARD_EVENT_STATUSES",
    "Transport",
    "TransportConnectionError",
    "TransportError",
    "capture",
    "capture_span",
    "current_flow_context",
    "flow_context",
    "get_default_client",
    "sanitize_metadata",
    "set_default_client",
]