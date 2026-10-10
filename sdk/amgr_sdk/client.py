from amgr_sdk.capture.recorder import EventRecorder
from amgr_sdk.config import AMGRConfig
from amgr_sdk.models.events import FlowEvent


class AMGRClient:
    """Thin facade over an application-provided event recorder."""

    def __init__(self, recorder: EventRecorder, config: AMGRConfig | None = None) -> None:
        self.config = config or AMGRConfig.from_env()
        self._recorder = recorder

    def record(self, event: FlowEvent) -> None:
        self._recorder.record(event)