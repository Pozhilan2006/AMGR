"""Placeholder lineage service for provenance queries."""


class LineageService:
    """Tracks flow ancestry and provenance across runtime events."""

    def __init__(self) -> None:
        self._implemented = False

    def resolve_lineage(self, *args, **kwargs):
        raise NotImplementedError("Lineage resolution is not implemented yet.")
