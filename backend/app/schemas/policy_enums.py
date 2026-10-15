"""Policy engine enumerations for AMGR.

Defines supported policy effects and condition operators following
the Member 3 Policy Engine & Runtime Enforcement specifications.
"""

from enum import Enum


class PolicyEffect(str, Enum):
    """Supported outcomes for a policy rule.

    - ALLOW: The evaluated action is permitted.
    - BLOCK: The evaluated action is blocked at the runtime boundary.
    - REQUIRE_APPROVAL: The evaluated action requires human/operator intervention.
    """

    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


class ConditionOperator(str, Enum):
    """Explicitly supported operators for deterministic condition evaluation.

    Arbitrary expression execution (e.g. eval, script blocks) is forbidden.
    """

    # Equality
    EQ = "EQ"
    NOT_EQ = "NOT_EQ"

    # Membership
    IN = "IN"
    NOT_IN = "NOT_IN"

    # String pattern matching
    CONTAINS = "CONTAINS"
    STARTS_WITH = "STARTS_WITH"
    ENDS_WITH = "ENDS_WITH"

    # Relational / Numeric comparisons
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"

    # Existence / Presence checks
    EXISTS = "EXISTS"
    NOT_EXISTS = "NOT_EXISTS"


class AttributeResolutionStatus(str, Enum):
    """Resolution status when extracting an attribute from the evaluation context."""

    FOUND = "FOUND"
    MISSING = "MISSING"
    INVALID_PATH = "INVALID_PATH"


class RuleResolutionAlgorithm(str, Enum):
    """Supported rule resolution strategies when evaluating multi-rule policies."""

    FIRST_APPLICABLE = "FIRST_APPLICABLE"
    BLOCK_OVERRIDES = "BLOCK_OVERRIDES"
    ALLOW_OVERRIDES = "ALLOW_OVERRIDES"


class PolicyChangeType(str, Enum):
    """Supported types of policy lifecycle change events."""

    CREATED = "CREATED"
    UPDATED = "UPDATED"
    ACTIVATED = "ACTIVATED"
    DEACTIVATED = "DEACTIVATED"
    ROLLED_BACK = "ROLLED_BACK"


