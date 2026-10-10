"""Placeholder rule matcher for policy conditions."""


class PolicyMatcher:
    """Determines whether policy conditions match a runtime event or request."""

    def __init__(self) -> None:
        self._implemented = False

    def match(self, *args, **kwargs):
        raise NotImplementedError("Policy matching is not implemented yet.")
