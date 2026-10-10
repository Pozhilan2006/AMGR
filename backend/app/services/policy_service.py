"""Placeholder policy service for deterministic evaluation lifecycle."""


class PolicyService:
    """Policy lifecycle management and evaluation orchestration."""

    def __init__(self) -> None:
        self._implemented = False

    def evaluate(self, *args, **kwargs):
        raise NotImplementedError("Policy evaluation is not implemented yet.")
