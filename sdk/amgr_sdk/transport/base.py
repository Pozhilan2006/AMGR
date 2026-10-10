from typing import Protocol

from amgr_sdk.models.events import FlowEvent


class Transport(Protocol):
    def send(self, event: FlowEvent) -> None:
        """Submit one event using an implementation supplied by the application."""