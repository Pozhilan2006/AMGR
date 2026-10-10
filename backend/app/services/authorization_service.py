"""Placeholder authorization service for runtime decisioning."""


class AuthorizationService:
    """Evaluates authorization requests before protected operations execute."""

    def __init__(self) -> None:
        self._implemented = False

    def authorize(self, *args, **kwargs):
        raise NotImplementedError("Authorization enforcement is not implemented yet.")
