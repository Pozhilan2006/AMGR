"""Placeholder precedence resolver for policy rule ordering."""


class PolicyPrecedence:
    """Orders policy rules and resolves conflicts deterministically."""

    def __init__(self) -> None:
        self._implemented = False

    def resolve(self, *args, **kwargs):
        raise NotImplementedError("Policy precedence resolution is not implemented yet.")
