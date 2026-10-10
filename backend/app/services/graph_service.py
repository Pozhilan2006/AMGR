"""Placeholder graph service for runtime flow analysis."""


class GraphService:
    """Graph persistence and traversal layer."""

    def __init__(self) -> None:
        self._implemented = False

    def load_graph(self, *args, **kwargs):
        raise NotImplementedError("Graph persistence and traversal are not implemented yet.")
