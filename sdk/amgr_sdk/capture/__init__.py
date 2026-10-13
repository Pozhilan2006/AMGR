"""Runtime capture interfaces."""

from amgr_sdk.capture.decorators import capture, capture_span
from amgr_sdk.capture.recorder import EventRecorder

__all__ = ["EventRecorder", "capture", "capture_span"]