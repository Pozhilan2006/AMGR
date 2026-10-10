"""Placeholder event ingestion service.

The actual implementation will validate, normalize, and persist runtime events.
"""


class EventIngestionService:
    """Inbound runtime event processing hook."""

    def __init__(self) -> None:
        self._implemented = False

    def ingest(self, event: object) -> object:
        raise NotImplementedError("Event ingestion is not implemented yet.")
