"""Deterministic policy evaluation engine for AMGR runtime enforcement.

Evaluates AuthorizationRequests against policies and condition ASTs deterministically.
Arbitrary code execution, dynamic string evaluation, and LLM-based overrides are strictly forbidden.
Missing or unknown security attributes never silently evaluate to ALLOW.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from backend.app.schemas.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
    ReasonCode,
)
from backend.app.schemas.condition import (
    ConditionGroup,
    ConditionRule,
    resolve_attribute,
)
from backend.app.schemas.policy import (
    PolicyCreate,
    PolicyRead,
    RuleDefinition,
    sort_policies,
)
from backend.app.schemas.policy_enums import (
    AttributeResolutionStatus,
    ConditionOperator,
    PolicyEffect,
    RuleResolutionAlgorithm,
)


class _ConditionResult:
    """Internal evaluation result carrying boolean outcome and diagnostics."""

    __slots__ = ("matched", "missing_attributes", "has_hard_mismatch")

    def __init__(
        self,
        matched: bool,
        missing_attributes: list[str] | None = None,
        has_hard_mismatch: bool = False,
    ) -> None:
        self.matched = matched
        self.missing_attributes = missing_attributes or []
        self.has_hard_mismatch = has_hard_mismatch


def _evaluate_condition_internal(
    condition: ConditionRule | ConditionGroup, context: Mapping[str, Any]
) -> _ConditionResult:
    if isinstance(condition, ConditionRule):
        status, actual = resolve_attribute(context, condition.field)

        # 1. Existence operators
        if condition.operator == ConditionOperator.EXISTS:
            if status == AttributeResolutionStatus.FOUND:
                return _ConditionResult(matched=True)
            elif status == AttributeResolutionStatus.MISSING:
                return _ConditionResult(
                    matched=False,
                    missing_attributes=[condition.field],
                    has_hard_mismatch=False,
                )
            else:  # INVALID_PATH
                return _ConditionResult(
                    matched=False,
                    missing_attributes=[],
                    has_hard_mismatch=True,
                )

        if condition.operator == ConditionOperator.NOT_EXISTS:
            if status == AttributeResolutionStatus.FOUND:
                return _ConditionResult(
                    matched=False,
                    missing_attributes=[],
                    has_hard_mismatch=True,
                )
            else:
                return _ConditionResult(matched=True)

        # 2. For all other operators, missing or invalid attribute fails closed
        if status == AttributeResolutionStatus.MISSING:
            return _ConditionResult(
                matched=False,
                missing_attributes=[condition.field],
                has_hard_mismatch=False,
            )

        if status == AttributeResolutionStatus.INVALID_PATH:
            return _ConditionResult(
                matched=False,
                missing_attributes=[],
                has_hard_mismatch=True,
            )

        expected = condition.value
        op = condition.operator

        # 3. Equality
        if op == ConditionOperator.EQ:
            matched = actual == expected
            return _ConditionResult(matched=matched, has_hard_mismatch=not matched)
        if op == ConditionOperator.NOT_EQ:
            matched = actual != expected
            return _ConditionResult(matched=matched, has_hard_mismatch=not matched)

        # 4. Membership
        if op == ConditionOperator.IN:
            if isinstance(expected, (list, tuple, set)):
                matched = actual in expected
                return _ConditionResult(matched=matched, has_hard_mismatch=not matched)
            return _ConditionResult(matched=False, has_hard_mismatch=True)
        if op == ConditionOperator.NOT_IN:
            if isinstance(expected, (list, tuple, set)):
                matched = actual not in expected
                return _ConditionResult(matched=matched, has_hard_mismatch=not matched)
            return _ConditionResult(matched=False, has_hard_mismatch=True)

        # 5. String pattern matching
        if op in (
            ConditionOperator.CONTAINS,
            ConditionOperator.STARTS_WITH,
            ConditionOperator.ENDS_WITH,
        ):
            if not isinstance(actual, str) or not isinstance(expected, str):
                return _ConditionResult(matched=False, has_hard_mismatch=True)
            if op == ConditionOperator.CONTAINS:
                matched = expected in actual
            elif op == ConditionOperator.STARTS_WITH:
                matched = actual.startswith(expected)
            elif op == ConditionOperator.ENDS_WITH:
                matched = actual.endswith(expected)
            else:
                matched = False
            return _ConditionResult(matched=matched, has_hard_mismatch=not matched)

        # 6. Relational numeric comparisons
        if op in (
            ConditionOperator.GT,
            ConditionOperator.GTE,
            ConditionOperator.LT,
            ConditionOperator.LTE,
        ):
            # Strict numeric typing: exclude booleans
            if (
                isinstance(actual, bool)
                or not isinstance(actual, (int, float))
                or isinstance(expected, bool)
                or not isinstance(expected, (int, float))
            ):
                return _ConditionResult(matched=False, has_hard_mismatch=True)

            if op == ConditionOperator.GT:
                matched = actual > expected
            elif op == ConditionOperator.GTE:
                matched = actual >= expected
            elif op == ConditionOperator.LT:
                matched = actual < expected
            elif op == ConditionOperator.LTE:
                matched = actual <= expected
            else:
                matched = False
            return _ConditionResult(matched=matched, has_hard_mismatch=not matched)

        return _ConditionResult(matched=False, has_hard_mismatch=True)

    elif isinstance(condition, ConditionGroup):
        missing_attrs: list[str] = []
        has_hard_mismatch = False
        all_passed = True

        # Compound group evaluation
        if condition.all_of is not None:
            all_of_passed = True
            all_of_missing: list[str] = []
            all_of_hard_mismatch = False
            for child in condition.all_of:
                res = _evaluate_condition_internal(child, context)
                if not res.matched:
                    all_of_passed = False
                    if res.has_hard_mismatch:
                        all_of_hard_mismatch = True
                    for attr in res.missing_attributes:
                        if attr not in all_of_missing:
                            all_of_missing.append(attr)
            if not all_of_passed:
                all_passed = False
                if all_of_hard_mismatch:
                    has_hard_mismatch = True
                else:
                    for attr in all_of_missing:
                        if attr not in missing_attrs:
                            missing_attrs.append(attr)

        if condition.any_of is not None:
            any_of_passed = False
            any_of_missing: list[str] = []
            for child in condition.any_of:
                res = _evaluate_condition_internal(child, context)
                if res.matched:
                    any_of_passed = True
                else:
                    for attr in res.missing_attributes:
                        if attr not in any_of_missing:
                            any_of_missing.append(attr)
            if not any_of_passed:
                all_passed = False
                if any_of_missing:
                    for attr in any_of_missing:
                        if attr not in missing_attrs:
                            missing_attrs.append(attr)
                else:
                    has_hard_mismatch = True

        if condition.none_of is not None:
            none_of_passed = True
            for child in condition.none_of:
                res = _evaluate_condition_internal(child, context)
                if res.matched:
                    none_of_passed = False
                    break
            if not none_of_passed:
                all_passed = False
                has_hard_mismatch = True

        if all_passed:
            return _ConditionResult(matched=True)
        else:
            return _ConditionResult(
                matched=False,
                missing_attributes=missing_attrs if not has_hard_mismatch else [],
                has_hard_mismatch=has_hard_mismatch,
            )

    return _ConditionResult(matched=False, has_hard_mismatch=True)


def evaluate_condition(
    condition: ConditionRule | ConditionGroup, context: Mapping[str, Any]
) -> bool:
    """Evaluates a condition AST (atomic rule or compound group) against context attributes.

    Deterministic rules:
    - If attribute resolution returns MISSING or INVALID_PATH:
      - EXISTS evaluates to False.
      - NOT_EXISTS evaluates to True.
      - All other operators evaluate to False. (Missing context never satisfies a positive match).
    - If attribute is FOUND:
      - Operates strictly according to the specified operator and operand types.
    """
    return _evaluate_condition_internal(condition, context).matched


class _RuleEvalResult:
    """Internal rule evaluation result carrying application status and diagnostics."""

    __slots__ = ("applicable", "matched", "missing_attributes", "materially_prevented")

    def __init__(
        self,
        applicable: bool,
        matched: bool,
        missing_attributes: list[str] | None = None,
        materially_prevented: bool = False,
    ) -> None:
        self.applicable = applicable
        self.matched = matched
        self.missing_attributes = missing_attributes or []
        self.materially_prevented = materially_prevented


def _evaluate_rule_internal(
    rule: RuleDefinition, request: AuthorizationRequest
) -> _RuleEvalResult:
    if not rule.enabled:
        return _RuleEvalResult(applicable=False, matched=False)

    # Check target actions scoping filter
    if rule.target_actions is not None:
        if request.action not in rule.target_actions:
            return _RuleEvalResult(applicable=False, matched=False)

    # Check target resources scoping filter
    if rule.target_resources is not None:
        if request.resource not in rule.target_resources:
            return _RuleEvalResult(applicable=False, matched=False)

    eval_ctx = request.to_eval_context()
    cond_res = _evaluate_condition_internal(rule.condition, eval_ctx)

    if cond_res.matched:
        return _RuleEvalResult(applicable=True, matched=True)

    # Condition did not match. Check if missing context materially prevented it.
    if cond_res.missing_attributes and not cond_res.has_hard_mismatch:
        return _RuleEvalResult(
            applicable=True,
            matched=False,
            missing_attributes=cond_res.missing_attributes,
            materially_prevented=True,
        )

    return _RuleEvalResult(applicable=True, matched=False)


def evaluate_rule(rule: RuleDefinition, request: AuthorizationRequest) -> bool:
    """Evaluates whether an individual rule applies to an authorization request."""
    return _evaluate_rule_internal(rule, request).matched


def evaluate_policy(
    policy: PolicyCreate | PolicyRead, request: AuthorizationRequest
) -> AuthorizationDecision:
    """Deterministically evaluates a multi-rule policy against an inbound authorization request.

    Resolution algorithms supported:
    - FIRST_APPLICABLE: Highest-priority matching rule dictates the decision.
    - BLOCK_OVERRIDES: If any matching rule yields BLOCK, outcome is BLOCK.
      Else if any yields REQUIRE_APPROVAL, outcome is REQUIRE_APPROVAL. Else ALLOW.
    - ALLOW_OVERRIDES: If any matching rule yields ALLOW, outcome is ALLOW.
      Else if any yields REQUIRE_APPROVAL, outcome is REQUIRE_APPROVAL. Else BLOCK.

    Fallback behavior:
    - When zero rules match or policy is disabled, strictly returns policy.default_effect (fail-closed).
    - If missing context materially prevented applicable rules from matching, emits ReasonCode.MISSING_CONTEXT.
    """
    now = datetime.now(timezone.utc)
    policy_version = getattr(policy, "version", None)

    if not policy.enabled:
        return AuthorizationDecision(
            decision=policy.default_effect,
            policy_id=policy.id,
            policy_version=policy_version,
            matched_rule_id=None,
            matched_rule_ids=[],
            matched_policy_ids=[],
            reason_code=ReasonCode.POLICY_DISABLED,
            reason_message=f"Policy '{policy.id}' is disabled; defaulting to fallback effect {policy.default_effect.value}.",
            missing_attributes=[],
            evaluated_at=now,
        )

    sorted_rules = policy.get_sorted_rules()

    if policy.resolution_algorithm == RuleResolutionAlgorithm.FIRST_APPLICABLE:
        missing_attrs: list[str] = []
        materially_prevented = False

        for rule in sorted_rules:
            res = _evaluate_rule_internal(rule, request)
            if res.matched:
                return AuthorizationDecision(
                    decision=rule.effect,
                    policy_id=policy.id,
                    policy_version=policy_version,
                    matched_rule_id=rule.id,
                    matched_rule_ids=[rule.id],
                    matched_policy_ids=[policy.id],
                    reason_code=ReasonCode.RULE_MATCHED,
                    reason_message=f"Rule '{rule.id}' matched with effect {rule.effect.value}.",
                    missing_attributes=[],
                    evaluated_at=now,
                )
            if res.materially_prevented:
                materially_prevented = True
                for attr in res.missing_attributes:
                    if attr not in missing_attrs:
                        missing_attrs.append(attr)

        # No rules matched
        if materially_prevented and missing_attrs:
            return AuthorizationDecision(
                decision=policy.default_effect,
                policy_id=policy.id,
                policy_version=policy_version,
                matched_rule_id=None,
                matched_rule_ids=[],
                matched_policy_ids=[],
                reason_code=ReasonCode.MISSING_CONTEXT,
                reason_message=(
                    f"Policy '{policy.id}' evaluation could not reach a rule outcome due to "
                    f"missing context attributes ({', '.join(missing_attrs)}); defaulting to fallback effect {policy.default_effect.value}."
                ),
                missing_attributes=missing_attrs,
                evaluated_at=now,
            )

        return AuthorizationDecision(
            decision=policy.default_effect,
            policy_id=policy.id,
            policy_version=policy_version,
            matched_rule_id=None,
            matched_rule_ids=[],
            matched_policy_ids=[],
            reason_code=ReasonCode.DEFAULT_FALLBACK,
            reason_message=f"No rules in policy '{policy.id}' matched request; defaulting to {policy.default_effect.value}.",
            missing_attributes=[],
            evaluated_at=now,
        )

    elif policy.resolution_algorithm == RuleResolutionAlgorithm.BLOCK_OVERRIDES:
        matched_rules: list[RuleDefinition] = []
        missing_attrs: list[str] = []
        materially_prevented = False

        for rule in sorted_rules:
            res = _evaluate_rule_internal(rule, request)
            if res.matched:
                matched_rules.append(rule)
            elif res.materially_prevented:
                materially_prevented = True
                for attr in res.missing_attributes:
                    if attr not in missing_attrs:
                        missing_attrs.append(attr)

        matched_ids = [r.id for r in matched_rules]

        if not matched_rules:
            if materially_prevented and missing_attrs:
                return AuthorizationDecision(
                    decision=policy.default_effect,
                    policy_id=policy.id,
                    policy_version=policy_version,
                    matched_rule_id=None,
                    matched_rule_ids=[],
                    matched_policy_ids=[],
                    reason_code=ReasonCode.MISSING_CONTEXT,
                    reason_message=(
                        f"Policy '{policy.id}' evaluation could not reach a rule outcome due to "
                        f"missing context attributes ({', '.join(missing_attrs)}); defaulting to fallback effect {policy.default_effect.value}."
                    ),
                    missing_attributes=missing_attrs,
                    evaluated_at=now,
                )

            return AuthorizationDecision(
                decision=policy.default_effect,
                policy_id=policy.id,
                policy_version=policy_version,
                matched_rule_id=None,
                matched_rule_ids=[],
                matched_policy_ids=[],
                reason_code=ReasonCode.DEFAULT_FALLBACK,
                reason_message=f"No rules in policy '{policy.id}' matched request; defaulting to {policy.default_effect.value}.",
                missing_attributes=[],
                evaluated_at=now,
            )

        # Check BLOCK first
        for rule in matched_rules:
            if rule.effect == PolicyEffect.BLOCK:
                return AuthorizationDecision(
                    decision=PolicyEffect.BLOCK,
                    policy_id=policy.id,
                    policy_version=policy_version,
                    matched_rule_id=rule.id,
                    matched_rule_ids=matched_ids,
                    matched_policy_ids=[policy.id],
                    reason_code=ReasonCode.RULE_MATCHED,
                    reason_message=f"Rule '{rule.id}' matched with overriding BLOCK effect.",
                    missing_attributes=[],
                    evaluated_at=now,
                )

        # Check REQUIRE_APPROVAL next
        for rule in matched_rules:
            if rule.effect == PolicyEffect.REQUIRE_APPROVAL:
                return AuthorizationDecision(
                    decision=PolicyEffect.REQUIRE_APPROVAL,
                    policy_id=policy.id,
                    policy_version=policy_version,
                    matched_rule_id=rule.id,
                    matched_rule_ids=matched_ids,
                    matched_policy_ids=[policy.id],
                    reason_code=ReasonCode.RULE_MATCHED,
                    reason_message=f"Rule '{rule.id}' matched with overriding REQUIRE_APPROVAL effect.",
                    missing_attributes=[],
                    evaluated_at=now,
                )

        # Otherwise ALLOW
        first_allow = matched_rules[0]
        return AuthorizationDecision(
            decision=PolicyEffect.ALLOW,
            policy_id=policy.id,
            policy_version=policy_version,
            matched_rule_id=first_allow.id,
            matched_rule_ids=matched_ids,
            matched_policy_ids=[policy.id],
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message=f"Rules {[r.id for r in matched_rules]} matched with ALLOW effect.",
            missing_attributes=[],
            evaluated_at=now,
        )

    elif policy.resolution_algorithm == RuleResolutionAlgorithm.ALLOW_OVERRIDES:
        matched_rules: list[RuleDefinition] = []
        missing_attrs: list[str] = []
        materially_prevented = False

        for rule in sorted_rules:
            res = _evaluate_rule_internal(rule, request)
            if res.matched:
                matched_rules.append(rule)
            elif res.materially_prevented:
                materially_prevented = True
                for attr in res.missing_attributes:
                    if attr not in missing_attrs:
                        missing_attrs.append(attr)

        matched_ids = [r.id for r in matched_rules]

        if not matched_rules:
            if materially_prevented and missing_attrs:
                return AuthorizationDecision(
                    decision=policy.default_effect,
                    policy_id=policy.id,
                    policy_version=policy_version,
                    matched_rule_id=None,
                    matched_rule_ids=[],
                    matched_policy_ids=[],
                    reason_code=ReasonCode.MISSING_CONTEXT,
                    reason_message=(
                        f"Policy '{policy.id}' evaluation could not reach a rule outcome due to "
                        f"missing context attributes ({', '.join(missing_attrs)}); defaulting to fallback effect {policy.default_effect.value}."
                    ),
                    missing_attributes=missing_attrs,
                    evaluated_at=now,
                )

            return AuthorizationDecision(
                decision=policy.default_effect,
                policy_id=policy.id,
                policy_version=policy_version,
                matched_rule_id=None,
                matched_rule_ids=[],
                matched_policy_ids=[],
                reason_code=ReasonCode.DEFAULT_FALLBACK,
                reason_message=f"No rules in policy '{policy.id}' matched request; defaulting to {policy.default_effect.value}.",
                missing_attributes=[],
                evaluated_at=now,
            )

        # Check ALLOW first
        for rule in matched_rules:
            if rule.effect == PolicyEffect.ALLOW:
                return AuthorizationDecision(
                    decision=PolicyEffect.ALLOW,
                    policy_id=policy.id,
                    policy_version=policy_version,
                    matched_rule_id=rule.id,
                    matched_rule_ids=matched_ids,
                    matched_policy_ids=[policy.id],
                    reason_code=ReasonCode.RULE_MATCHED,
                    reason_message=f"Rule '{rule.id}' matched with overriding ALLOW effect.",
                    missing_attributes=[],
                    evaluated_at=now,
                )

        # Check REQUIRE_APPROVAL next
        for rule in matched_rules:
            if rule.effect == PolicyEffect.REQUIRE_APPROVAL:
                return AuthorizationDecision(
                    decision=PolicyEffect.REQUIRE_APPROVAL,
                    policy_id=policy.id,
                    policy_version=policy_version,
                    matched_rule_id=rule.id,
                    matched_rule_ids=matched_ids,
                    matched_policy_ids=[policy.id],
                    reason_code=ReasonCode.RULE_MATCHED,
                    reason_message=f"Rule '{rule.id}' matched with overriding REQUIRE_APPROVAL effect.",
                    missing_attributes=[],
                    evaluated_at=now,
                )

        # Otherwise BLOCK
        first_block = matched_rules[0]
        return AuthorizationDecision(
            decision=PolicyEffect.BLOCK,
            policy_id=policy.id,
            policy_version=policy_version,
            matched_rule_id=first_block.id,
            matched_rule_ids=matched_ids,
            matched_policy_ids=[policy.id],
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message=f"Rules {[r.id for r in matched_rules]} matched with BLOCK effect.",
            missing_attributes=[],
            evaluated_at=now,
        )

    # Fallback safety guard
    return AuthorizationDecision(
        decision=policy.default_effect,
        policy_id=policy.id,
        policy_version=policy_version,
        matched_rule_id=None,
        matched_rule_ids=[],
        matched_policy_ids=[],
        reason_code=ReasonCode.DEFAULT_FALLBACK,
        reason_message=f"Fallback decision rendered for policy '{policy.id}'.",
        missing_attributes=[],
        evaluated_at=now,
    )


def evaluate_policies(
    policies: Sequence[PolicyCreate | PolicyRead],
    request: AuthorizationRequest,
    default_effect: PolicyEffect = PolicyEffect.BLOCK,
    conflict_algorithm: RuleResolutionAlgorithm = RuleResolutionAlgorithm.BLOCK_OVERRIDES,
) -> AuthorizationDecision:
    """Deterministically evaluates a collection of policies against an inbound request.

    Priority and conflict resolution rules:
    - Filters to enabled policies. If none enabled or collection empty, returns default_effect (BLOCK).
    - Sorts candidate policies primarily by priority descending (higher values take precedence).
    - Tie-breaks equal priorities deterministically by policy ID ascending.
    - Groups policies by priority tier; evaluates highest priority tier first.
    - If any policies in a priority tier match:
      - If exactly one matches: that policy's decision is rendered.
      - If multiple match with the same effect: unanimous decision rendered; tie-breaks primary ID.
      - If multiple match with conflicting effects: resolved via conflict_algorithm:
        - BLOCK_OVERRIDES (default): BLOCK overrides REQUIRE_APPROVAL and ALLOW; REQUIRE_APPROVAL overrides ALLOW.
        - ALLOW_OVERRIDES: ALLOW overrides REQUIRE_APPROVAL and BLOCK; REQUIRE_APPROVAL overrides BLOCK.
        - FIRST_APPLICABLE: First applicable policy in deterministic order dictates the decision.
        Sets reason_code to CONFLICT_RESOLVED with detailed justification.
      - Lower priority tiers are ignored once a higher priority tier has matched.
    - If zero policies match across all tiers: falls back to default_effect (BLOCK).
    - If missing context materially prevented applicable policies from matching, emits ReasonCode.MISSING_CONTEXT.
    """
    now = datetime.now(timezone.utc)

    if not policies:
        return AuthorizationDecision(
            decision=default_effect,
            policy_id=None,
            policy_version=None,
            matched_rule_id=None,
            matched_rule_ids=[],
            matched_policy_ids=[],
            reason_code=ReasonCode.DEFAULT_FALLBACK,
            reason_message=f"No policies provided; defaulting to fallback effect {default_effect.value}.",
            missing_attributes=[],
            evaluated_at=now,
        )

    enabled_policies = [p for p in policies if p.enabled]
    if not enabled_policies:
        return AuthorizationDecision(
            decision=default_effect,
            policy_id=None,
            policy_version=None,
            matched_rule_id=None,
            matched_rule_ids=[],
            matched_policy_ids=[],
            reason_code=ReasonCode.POLICY_DISABLED,
            reason_message=f"All {len(policies)} provided policies are disabled; defaulting to fallback effect {default_effect.value}.",
            missing_attributes=[],
            evaluated_at=now,
        )

    sorted_candidates = sort_policies(enabled_policies)

    # Collect distinct priority tiers descending
    priority_tiers: list[int] = []
    for p in sorted_candidates:
        if p.priority not in priority_tiers:
            priority_tiers.append(p.priority)

    all_missing_attributes: list[str] = []
    had_missing_context_failure = False

    for priority in priority_tiers:
        tier_policies = [p for p in sorted_candidates if p.priority == priority]
        tier_matches: list[tuple[PolicyCreate | PolicyRead, AuthorizationDecision]] = []

        for p in tier_policies:
            decision = evaluate_policy(p, request)
            if decision.reason_code == ReasonCode.RULE_MATCHED:
                tier_matches.append((p, decision))
            elif decision.reason_code == ReasonCode.MISSING_CONTEXT:
                had_missing_context_failure = True
                for attr in decision.missing_attributes:
                    if attr not in all_missing_attributes:
                        all_missing_attributes.append(attr)

        if not tier_matches:
            # No matching policies at this priority tier; evaluate next tier
            continue

        all_matched_policy_ids = [p.id for p, _ in tier_matches]
        all_matched_rule_ids = [
            r_id for _, d in tier_matches for r_id in d.matched_rule_ids
        ]

        # Case 1: Exactly one policy matched in this priority tier
        if len(tier_matches) == 1:
            win_p, win_d = tier_matches[0]
            return AuthorizationDecision(
                decision=win_d.decision,
                policy_id=win_p.id,
                policy_version=getattr(win_p, "version", None),
                matched_rule_id=win_d.matched_rule_id,
                matched_rule_ids=win_d.matched_rule_ids,
                matched_policy_ids=[win_p.id],
                reason_code=ReasonCode.RULE_MATCHED,
                reason_message=f"Policy '{win_p.id}' (priority {win_p.priority}) matched with effect {win_d.decision.value}.",
                missing_attributes=[],
                evaluated_at=now,
            )

        distinct_effects = {d.decision for _, d in tier_matches}

        # Case 2A: All matching policies in this tier agree on outcome
        if len(distinct_effects) == 1:
            win_p, win_d = tier_matches[0]
            return AuthorizationDecision(
                decision=win_d.decision,
                policy_id=win_p.id,
                policy_version=getattr(win_p, "version", None),
                matched_rule_id=win_d.matched_rule_id,
                matched_rule_ids=all_matched_rule_ids,
                matched_policy_ids=all_matched_policy_ids,
                reason_code=ReasonCode.RULE_MATCHED,
                reason_message=(
                    f"All {len(tier_matches)} matching policies at priority {win_p.priority} "
                    f"agreed on effect {win_d.decision.value} (primary: '{win_p.id}')."
                ),
                missing_attributes=[],
                evaluated_at=now,
            )

        # Case 2B: Conflicting outcomes across equal-priority policies
        effects_summary = [f"'{p.id}'={d.decision.value}" for p, d in tier_matches]

        if conflict_algorithm == RuleResolutionAlgorithm.BLOCK_OVERRIDES:
            if PolicyEffect.BLOCK in distinct_effects:
                win_p, win_d = next(
                    (p, d) for p, d in tier_matches if d.decision == PolicyEffect.BLOCK
                )
            elif PolicyEffect.REQUIRE_APPROVAL in distinct_effects:
                win_p, win_d = next(
                    (p, d)
                    for p, d in tier_matches
                    if d.decision == PolicyEffect.REQUIRE_APPROVAL
                )
            else:
                win_p, win_d = tier_matches[0]

            return AuthorizationDecision(
                decision=win_d.decision,
                policy_id=win_p.id,
                policy_version=getattr(win_p, "version", None),
                matched_rule_id=win_d.matched_rule_id,
                matched_rule_ids=all_matched_rule_ids,
                matched_policy_ids=all_matched_policy_ids,
                reason_code=ReasonCode.CONFLICT_RESOLVED,
                reason_message=(
                    f"Conflict among policies at priority {win_p.priority} ({', '.join(effects_summary)}) "
                    f"resolved to {win_d.decision.value} via BLOCK_OVERRIDES (deciding policy: '{win_p.id}')."
                ),
                missing_attributes=[],
                evaluated_at=now,
            )

        elif conflict_algorithm == RuleResolutionAlgorithm.ALLOW_OVERRIDES:
            if PolicyEffect.ALLOW in distinct_effects:
                win_p, win_d = next(
                    (p, d) for p, d in tier_matches if d.decision == PolicyEffect.ALLOW
                )
            elif PolicyEffect.REQUIRE_APPROVAL in distinct_effects:
                win_p, win_d = next(
                    (p, d)
                    for p, d in tier_matches
                    if d.decision == PolicyEffect.REQUIRE_APPROVAL
                )
            else:
                win_p, win_d = tier_matches[0]

            return AuthorizationDecision(
                decision=win_d.decision,
                policy_id=win_p.id,
                policy_version=getattr(win_p, "version", None),
                matched_rule_id=win_d.matched_rule_id,
                matched_rule_ids=all_matched_rule_ids,
                matched_policy_ids=all_matched_policy_ids,
                reason_code=ReasonCode.CONFLICT_RESOLVED,
                reason_message=(
                    f"Conflict among policies at priority {win_p.priority} ({', '.join(effects_summary)}) "
                    f"resolved to {win_d.decision.value} via ALLOW_OVERRIDES (deciding policy: '{win_p.id}')."
                ),
                missing_attributes=[],
                evaluated_at=now,
            )

        elif conflict_algorithm == RuleResolutionAlgorithm.FIRST_APPLICABLE:
            win_p, win_d = tier_matches[0]
            return AuthorizationDecision(
                decision=win_d.decision,
                policy_id=win_p.id,
                policy_version=getattr(win_p, "version", None),
                matched_rule_id=win_d.matched_rule_id,
                matched_rule_ids=all_matched_rule_ids,
                matched_policy_ids=all_matched_policy_ids,
                reason_code=ReasonCode.CONFLICT_RESOLVED,
                reason_message=(
                    f"Conflict among policies at priority {win_p.priority} ({', '.join(effects_summary)}) "
                    f"resolved to first applicable policy '{win_p.id}' ({win_d.decision.value}) by deterministic ID tie-breaker."
                ),
                missing_attributes=[],
                evaluated_at=now,
            )

    # Zero matches across all priority tiers
    if had_missing_context_failure and all_missing_attributes:
        return AuthorizationDecision(
            decision=default_effect,
            policy_id=None,
            policy_version=None,
            matched_rule_id=None,
            matched_rule_ids=[],
            matched_policy_ids=[],
            reason_code=ReasonCode.MISSING_CONTEXT,
            reason_message=(
                f"Evaluation across {len(sorted_candidates)} policies could not reach a rule outcome due to "
                f"missing context attributes ({', '.join(all_missing_attributes)}); defaulting to fallback effect {default_effect.value}."
            ),
            missing_attributes=all_missing_attributes,
            evaluated_at=now,
        )

    return AuthorizationDecision(
        decision=default_effect,
        policy_id=None,
        policy_version=None,
        matched_rule_id=None,
        matched_rule_ids=[],
        matched_policy_ids=[],
        reason_code=ReasonCode.DEFAULT_FALLBACK,
        reason_message=f"No policies matched request; defaulting to fallback effect {default_effect.value}.",
        missing_attributes=[],
        evaluated_at=now,
    )

