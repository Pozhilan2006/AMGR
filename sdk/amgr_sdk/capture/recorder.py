from amgr_sdk.models.events import FlowEvent
from amgr_sdk.transport.base import Transport


class EventRecorder:
    """Forwards an event to an injected transport; capture is not automatic."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def record(self, event: FlowEvent) -> None:
        self._transport.send(event)