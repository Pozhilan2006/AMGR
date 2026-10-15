"""Policy lifecycle management service for AMGR.

Implements policy CRUD operations, application and environment scope isolation,
declarative condition safety validation, and lifecycle transition management
following Member 3 Phase 4 specifications.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from backend.app.repositories.policy_repository import (
    PolicyPersistenceError,
    PolicyRepository,
)
from backend.app.schemas.condition import ConditionGroup, ConditionRule
from backend.app.policy_engine.evaluator import (
    detect_policy_conflicts,
    simulate_policy_evaluation,
)
from backend.app.schemas.authorization import AuthorizationRequest
from backend.app.schemas.policy import (
    ConflictDetectionResult,
    FieldChange,
    PolicyCreate,
    PolicyDiff,
    PolicyHistoryEntry,
    PolicyRead,
    PolicySimulationResponse,
    PolicyUpdate,
    RuleDefinition,
    RuleDiff,
    RuleSimulationTrace,
    sort_policies,
)
from backend.app.schemas.policy_enums import ConditionOperator
from backend.app.services.approval_service import ApprovalService

# Security patterns to reject arbitrary code execution and SQL injection in condition values/fields
_UNSAFE_PYTHON_PATTERN = re.compile(
    r"(?i)\b(eval|exec|compile|getattr|setattr|delattr|__import__|open)\s*\(|"
    r"\b(subprocess|os\.system|os\.popen|os\.spawn|sys\.modules)\b|"
    r"\b(globals|locals|vars|builtins)\s*\(|"
    r"\bimport\s+[a-zA-Z_]|"
    r"__\w+__|"
    r"\blambda\s+"
)

_UNSAFE_SQL_PATTERN = re.compile(
    r"(?i);\s*(drop|delete|truncate|insert|update|alter|create|select)\s+|"
    r"\bunion\s+(all\s+)?select\b|"
    r";\s*--|--\s*$|"
    r"/\*.*?\*/|"
    r"\b(?:or|and)\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+['\"]?|"
    r"\bexec(?:ute)?\s*\("
)


def _scan_for_unsafe_content(val: Any, path: str = "") -> None:
    """Recursively scans values for code injection or SQL fragments."""
    if isinstance(val, str):
        if _UNSAFE_PYTHON_PATTERN.search(val):
            raise ValueError(
                f"Arbitrary executable expression or Python code pattern detected at {path or 'root'}: {val!r}"
            )
        if _UNSAFE_SQL_PATTERN.search(val):
            raise ValueError(
                f"SQL fragment or injection pattern detected at {path or 'root'}: {val!r}"
            )
    elif isinstance(val, (list, tuple, set)):
        for idx, item in enumerate(val):
            _scan_for_unsafe_content(item, f"{path}[{idx}]")
    elif isinstance(val, dict):
        for k, v in val.items():
            _scan_for_unsafe_content(k, f"{path}.<key:{k}>")
            _scan_for_unsafe_content(v, f"{path}.{k}")


class PolicyService:
    """Policy lifecycle management service.

    Guarantees:
    - Application and environment scope isolation.
    - Strict validation against unsafe code execution and SQL fragments.
    - Transactional state updates and monotonic revision tracking via PolicyRepository.
    - Approval request invalidation upon policy deactivation or deletion.
    - Preservation of deterministic evaluation invariants.
    """

    def __init__(
        self,
        policy_repo: PolicyRepository | None = None,
        approval_service: ApprovalService | None = None,
    ) -> None:
        self.policy_repo = policy_repo if policy_repo is not None else PolicyRepository()
        self.approval_service = (
            approval_service if approval_service is not None else ApprovalService()
        )

    def validate_policy_safety(self, policy: PolicyCreate | PolicyUpdate) -> None:
        """Validates incoming policy data against arbitrary code and SQL fragments.

        Raises:
            ValueError: If unsafe content is discovered in fields, rules, or condition AST.
        """
        if policy.name:
            _scan_for_unsafe_content(policy.name, "name")
        if policy.description:
            _scan_for_unsafe_content(policy.description, "description")

        if policy.rules is not None:
            for rule in policy.rules:
                _scan_for_unsafe_content(rule.id, f"rule[{rule.id}].id")
                _scan_for_unsafe_content(rule.name, f"rule[{rule.id}].name")
                if rule.description:
                    _scan_for_unsafe_content(
                        rule.description, f"rule[{rule.id}].description"
                    )
                if rule.target_actions:
                    _scan_for_unsafe_content(
                        rule.target_actions, f"rule[{rule.id}].target_actions"
                    )
                if rule.target_resources:
                    _scan_for_unsafe_content(
                        rule.target_resources, f"rule[{rule.id}].target_resources"
                    )

                self._validate_condition_ast_safety(
                    rule.condition, f"rule[{rule.id}].condition"
                )

    def _validate_condition_ast_safety(
        self, condition: ConditionRule | ConditionGroup, path: str
    ) -> None:
        """Recursively checks condition AST nodes for prohibited executable expressions."""
        if isinstance(condition, ConditionRule):
            _scan_for_unsafe_content(condition.field, f"{path}.field")
            if condition.value is not None:
                _scan_for_unsafe_content(condition.value, f"{path}.value")
        elif isinstance(condition, ConditionGroup):
            for branch_name, branch in (
                ("all_of", condition.all_of),
                ("any_of", condition.any_of),
                ("none_of", condition.none_of),
            ):
                if branch:
                    for idx, child in enumerate(branch):
                        self._validate_condition_ast_safety(
                            child, f"{path}.{branch_name}[{idx}]"
                        )

    def is_policy_in_scope(
        self,
        policy: PolicyRead,
        application_id: str | None = None,
        environment: str | None = None,
    ) -> bool:
        """Determines whether a policy applies within the specified application and environment scope.

        A policy applies to a scope if at least one of its enabled rules applies to that scope.
        Rules without scope constraints are global and apply to all scopes.
        Rules with explicit application_id or environment condition requirements must match the scope.
        """
        if application_id is None and environment is None:
            return True

        if application_id == "*" or environment == "*":
            return True

        for rule in policy.rules:
            if not rule.enabled:
                continue

            # Check target_resources for application_id scoping if defined
            if application_id is not None and rule.target_resources:
                app_targets = [
                    res for res in rule.target_resources
                    if res == application_id
                    or res.startswith("app:")
                    or res.startswith("application:")
                ]
                if app_targets:
                    # Explicit application targeting is present; check if current app matches
                    matches_app = any(
                        res == application_id
                        or res == f"app:{application_id}"
                        or res == f"application:{application_id}"
                        for res in app_targets
                    )
                    if not matches_app:
                        continue

            # Check condition AST constraints on application_id and environment
            if not self._is_condition_compatible_with_scope(
                rule.condition, application_id=application_id, environment=environment
            ):
                continue

            return True

        return False

    def _is_condition_compatible_with_scope(
        self,
        condition: ConditionRule | ConditionGroup,
        application_id: str | None = None,
        environment: str | None = None,
    ) -> bool:
        """Evaluates whether condition AST constraints conflict with the requested scope."""
        if isinstance(condition, ConditionRule):
            field = condition.field
            val = condition.value
            op = condition.operator

            if application_id is not None and field in (
                "context.application_id",
                "application_id",
            ):
                if op == ConditionOperator.EQ and str(val) != application_id:
                    return False
                if op == ConditionOperator.IN and isinstance(val, (list, tuple, set)) and application_id not in val:
                    return False
                if op == ConditionOperator.NOT_EQ and str(val) == application_id:
                    return False
                if op == ConditionOperator.NOT_IN and isinstance(val, (list, tuple, set)) and application_id in val:
                    return False

            if environment is not None and field in (
                "context.environment",
                "environment",
            ):
                if op == ConditionOperator.EQ and str(val) != environment:
                    return False
                if op == ConditionOperator.IN and isinstance(val, (list, tuple, set)) and environment not in val:
                    return False
                if op == ConditionOperator.NOT_EQ and str(val) == environment:
                    return False
                if op == ConditionOperator.NOT_IN and isinstance(val, (list, tuple, set)) and environment in val:
                    return False

            return True

        elif isinstance(condition, ConditionGroup):
            # For all_of, all child conditions must be compatible
            if condition.all_of:
                if not all(
                    self._is_condition_compatible_with_scope(c, application_id, environment)
                    for c in condition.all_of
                ):
                    return False

            # For any_of, at least one child must be compatible
            if condition.any_of:
                if not any(
                    self._is_condition_compatible_with_scope(c, application_id, environment)
                    for c in condition.any_of
                ):
                    return False

            return True

        return True

    def list_policies(
        self,
        application_id: str | None = None,
        environment: str | None = None,
        authorized_scopes: Sequence[Any] | None = None,
        enabled_only: bool | None = None,
        action: str | None = None,
        resource: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[PolicyRead]:
        """Lists policies filtered by authorized application/environment scope and optional attributes."""
        all_policies = self.policy_repo.list_all_policies()
        filtered: list[PolicyRead] = []

        for policy in all_policies:
            if enabled_only and not policy.enabled:
                continue

            if action is not None or resource is not None:
                if not PolicyRepository._is_policy_applicable(
                    policy, action=action, resource=resource
                ):
                    continue

            # Scope filtering
            if application_id is not None or environment is not None:
                if not self.is_policy_in_scope(
                    policy, application_id=application_id, environment=environment
                ):
                    continue
            elif authorized_scopes is not None:
                # Check if policy is in scope for any of caller's authorized scopes
                in_any = any(
                    self.is_policy_in_scope(
                        policy,
                        application_id=getattr(s, "application_id", None)
                        if not isinstance(s, dict)
                        else s.get("application_id"),
                        environment=getattr(s, "environment", None)
                        if not isinstance(s, dict)
                        else s.get("environment"),
                    )
                    for s in authorized_scopes
                )
                if not in_any:
                    continue

            filtered.append(policy)

        sorted_policies = sort_policies(filtered)
        if offset is not None and offset > 0:
            sorted_policies = sorted_policies[offset:]
        if limit is not None and limit >= 0:
            sorted_policies = sorted_policies[:limit]
        return sorted_policies

    def get_policy(
        self,
        policy_id: str,
        version: int | None = None,
        authorized_scopes: Sequence[Any] | None = None,
    ) -> PolicyRead | None:
        """Retrieves an individual policy by ID, validating scope isolation."""
        policy = self.policy_repo.get_policy(policy_id, version=version)
        if policy is None:
            return None

        if authorized_scopes is not None:
            in_any = any(
                self.is_policy_in_scope(
                    policy,
                    application_id=getattr(s, "application_id", None)
                    if not isinstance(s, dict)
                    else s.get("application_id"),
                    environment=getattr(s, "environment", None)
                    if not isinstance(s, dict)
                    else s.get("environment"),
                )
                for s in authorized_scopes
            )
            if not in_any:
                raise PermissionError(
                    f"Policy '{policy_id}' is outside caller's authorized application/environment scope."
                )

        return policy

    def get_policy_revisions(
        self,
        policy_id: str,
        authorized_scopes: Sequence[Any] | None = None,
    ) -> list[PolicyRead]:
        """Returns all historical revisions for a policy in ascending version order, verifying scope."""
        revisions = self.policy_repo.get_policy_revisions(policy_id)
        if not revisions:
            return []

        # Scope validation using the latest revision
        latest = revisions[-1]
        if authorized_scopes is not None:
            in_any = any(
                self.is_policy_in_scope(
                    latest,
                    application_id=getattr(s, "application_id", None)
                    if not isinstance(s, dict)
                    else s.get("application_id"),
                    environment=getattr(s, "environment", None)
                    if not isinstance(s, dict)
                    else s.get("environment"),
                )
                for s in authorized_scopes
            )
            if not in_any:
                raise PermissionError(
                    f"Policy '{policy_id}' is outside caller's authorized application/environment scope."
                )

        return revisions

    def get_change_history(
        self,
        policy_id: str,
        authorized_scopes: Sequence[Any] | None = None,
    ) -> list[PolicyHistoryEntry]:
        """Returns audit change history entries for a policy in ascending chronological order, verifying scope."""
        history = self.policy_repo.get_change_history(policy_id)
        if not history:
            return []

        revisions = self.policy_repo.get_policy_revisions(policy_id)
        if revisions and authorized_scopes is not None:
            latest = revisions[-1]
            in_any = any(
                self.is_policy_in_scope(
                    latest,
                    application_id=getattr(s, "application_id", None)
                    if not isinstance(s, dict)
                    else s.get("application_id"),
                    environment=getattr(s, "environment", None)
                    if not isinstance(s, dict)
                    else s.get("environment"),
                )
                for s in authorized_scopes
            )
            if not in_any:
                raise PermissionError(
                    f"Policy '{policy_id}' is outside caller's authorized application/environment scope."
                )

        return history

    def _compute_diff(
        self,
        policy_id: str,
        from_policy: PolicyRead | PolicyCreate,
        to_policy: PolicyRead | PolicyCreate,
        from_version: int,
        to_version: int | None,
    ) -> PolicyDiff:
        """Computes structural and semantic differences between two policy definitions."""
        metadata_changes: dict[str, FieldChange] = {}
        if from_policy.name != to_policy.name:
            metadata_changes["name"] = FieldChange(old=from_policy.name, new=to_policy.name)
        if from_policy.description != to_policy.description:
            metadata_changes["description"] = FieldChange(
                old=from_policy.description, new=to_policy.description
            )
        if from_policy.priority != to_policy.priority:
            metadata_changes["priority"] = FieldChange(
                old=from_policy.priority, new=to_policy.priority
            )
        if from_policy.enabled != to_policy.enabled:
            metadata_changes["enabled"] = FieldChange(
                old=from_policy.enabled, new=to_policy.enabled
            )
        if from_policy.resolution_algorithm != to_policy.resolution_algorithm:
            metadata_changes["resolution_algorithm"] = FieldChange(
                old=from_policy.resolution_algorithm.value,
                new=to_policy.resolution_algorithm.value,
            )
        if from_policy.default_effect != to_policy.default_effect:
            metadata_changes["default_effect"] = FieldChange(
                old=from_policy.default_effect.value,
                new=to_policy.default_effect.value,
            )

        from_rules = {r.id: r for r in from_policy.rules}
        to_rules = {r.id: r for r in to_policy.rules}

        rules_added = [to_rules[rid] for rid in to_rules if rid not in from_rules]
        rules_removed = [from_rules[rid] for rid in from_rules if rid not in to_rules]

        rules_modified: list[RuleDiff] = []
        for rid in from_rules:
            if rid not in to_rules:
                continue
            r_old = from_rules[rid]
            r_new = to_rules[rid]
            changes: dict[str, FieldChange] = {}

            if r_old.name != r_new.name:
                changes["name"] = FieldChange(old=r_old.name, new=r_new.name)
            if r_old.description != r_new.description:
                changes["description"] = FieldChange(old=r_old.description, new=r_new.description)
            if r_old.effect != r_new.effect:
                changes["effect"] = FieldChange(old=r_old.effect.value, new=r_new.effect.value)
            if r_old.priority != r_new.priority:
                changes["priority"] = FieldChange(old=r_old.priority, new=r_new.priority)
            if r_old.enabled != r_new.enabled:
                changes["enabled"] = FieldChange(old=r_old.enabled, new=r_new.enabled)
            if r_old.target_actions != r_new.target_actions:
                changes["target_actions"] = FieldChange(old=r_old.target_actions, new=r_new.target_actions)
            if r_old.target_resources != r_new.target_resources:
                changes["target_resources"] = FieldChange(old=r_old.target_resources, new=r_new.target_resources)

            cond_old = r_old.condition.model_dump(mode="json") if hasattr(r_old.condition, "model_dump") else r_old.condition
            cond_new = r_new.condition.model_dump(mode="json") if hasattr(r_new.condition, "model_dump") else r_new.condition
            if cond_old != cond_new:
                changes["condition"] = FieldChange(old=cond_old, new=cond_new)

            if changes:
                rules_modified.append(RuleDiff(rule_id=rid, change_type="MODIFIED", changes=changes))

        has_changes = bool(metadata_changes or rules_added or rules_removed or rules_modified)
        return PolicyDiff(
            policy_id=policy_id,
            from_version=from_version,
            to_version=to_version,
            has_changes=has_changes,
            metadata_changes=metadata_changes,
            rules_added=rules_added,
            rules_removed=rules_removed,
            rules_modified=rules_modified,
        )

    def compare_policy_versions(
        self,
        policy_id: str,
        from_version: int,
        to_version: int | None = None,
        authorized_scopes: Sequence[Any] | None = None,
    ) -> PolicyDiff:
        """Compares two historical policy revisions and computes semantic differences."""
        from_policy = self.policy_repo.get_policy(policy_id, version=from_version)
        if from_policy is None:
            raise KeyError(f"Policy '{policy_id}' revision {from_version} not found.")

        if to_version is not None:
            to_policy = self.policy_repo.get_policy(policy_id, version=to_version)
            if to_policy is None:
                raise KeyError(f"Policy '{policy_id}' revision {to_version} not found.")
        else:
            to_policy = self.policy_repo.get_policy(policy_id)
            if to_policy is None:
                revisions = self.policy_repo.get_policy_revisions(policy_id)
                if not revisions:
                    raise KeyError(f"Policy '{policy_id}' not found.")
                to_policy = revisions[-1]

        if authorized_scopes is not None:
            for p in (from_policy, to_policy):
                if not any(
                    self.is_policy_in_scope(
                        p,
                        application_id=getattr(s, "application_id", None)
                        if not isinstance(s, dict)
                        else s.get("application_id"),
                        environment=getattr(s, "environment", None)
                        if not isinstance(s, dict)
                        else s.get("environment"),
                    )
                    for s in authorized_scopes
                ):
                    raise PermissionError(
                        f"Policy '{policy_id}' is outside caller's authorized application/environment scope."
                    )

        return self._compute_diff(
            policy_id=policy_id,
            from_policy=from_policy,
            to_policy=to_policy,
            from_version=from_policy.version,
            to_version=to_policy.version,
        )

    def preview_policy_update(
        self,
        policy_id: str,
        update: PolicyUpdate,
        base_version: int | None = None,
        authorized_scopes: Sequence[Any] | None = None,
    ) -> PolicyDiff:
        """Previews structural and rule changes that would be introduced by a proposed update."""
        if base_version is not None:
            base_policy = self.policy_repo.get_policy(policy_id, version=base_version)
            if base_policy is None:
                raise KeyError(f"Policy '{policy_id}' revision {base_version} not found.")
        else:
            base_policy = self.policy_repo.get_policy(policy_id)
            if base_policy is None:
                raise KeyError(f"Policy '{policy_id}' not found.")

        if authorized_scopes is not None:
            if not any(
                self.is_policy_in_scope(
                    base_policy,
                    application_id=getattr(s, "application_id", None)
                    if not isinstance(s, dict)
                    else s.get("application_id"),
                    environment=getattr(s, "environment", None)
                    if not isinstance(s, dict)
                    else s.get("environment"),
                )
                for s in authorized_scopes
            ):
                raise PermissionError(
                    f"Policy '{policy_id}' is outside caller's authorized scope."
                )

        self.validate_policy_safety(update)

        target_policy = PolicyRead(
            id=policy_id,
            name=update.name if update.name is not None else base_policy.name,
            description=update.description if update.description is not None else base_policy.description,
            priority=update.priority if update.priority is not None else base_policy.priority,
            rules=update.rules if update.rules is not None else base_policy.rules,
            resolution_algorithm=update.resolution_algorithm if update.resolution_algorithm is not None else base_policy.resolution_algorithm,
            default_effect=update.default_effect if update.default_effect is not None else base_policy.default_effect,
            enabled=update.enabled if update.enabled is not None else base_policy.enabled,
            version=base_policy.version + 1,
            created_at=base_policy.created_at,
            updated_at=base_policy.updated_at,
        )

        return self._compute_diff(
            policy_id=policy_id,
            from_policy=base_policy,
            to_policy=target_policy,
            from_version=base_policy.version,
            to_version=None,
        )

    def create_policy(
        self,
        policy: PolicyCreate,
        actor: str | None = None,
        authorized_scopes: Sequence[Any] | None = None,
        description: str | None = None,
    ) -> PolicyRead:
        """Creates a new policy revision (v1) with security and scope validation."""
        self.validate_policy_safety(policy)

        # Check that caller has authorization for any scopes targeted by this policy's rules
        if authorized_scopes is not None:
            # Create a mock PolicyRead to evaluate scope targeting
            mock_read = PolicyRead(
                id=policy.id,
                name=policy.name,
                description=policy.description,
                priority=policy.priority,
                rules=policy.rules,
                resolution_algorithm=policy.resolution_algorithm,
                default_effect=policy.default_effect,
                enabled=policy.enabled,
                version=1,
            )
            # If the policy specifies rules for specific scopes, caller must be authorized for at least one
            matches_any = any(
                self.is_policy_in_scope(
                    mock_read,
                    application_id=getattr(s, "application_id", None)
                    if not isinstance(s, dict)
                    else s.get("application_id"),
                    environment=getattr(s, "environment", None)
                    if not isinstance(s, dict)
                    else s.get("environment"),
                )
                for s in authorized_scopes
            )
            if not matches_any:
                raise PermissionError(
                    "Cannot create policy with rules targeting an application or environment scope outside caller's authorized scopes."
                )

        return self.policy_repo.create_policy(
            policy, actor=actor, description=description
        )

    def update_policy(
        self,
        policy_id: str,
        update: PolicyUpdate,
        actor: str | None = None,
        authorized_scopes: Sequence[Any] | None = None,
        description: str | None = None,
    ) -> PolicyRead:
        """Updates an existing policy, creating an immutable revision and managing lifecycle transitions."""
        current = self.policy_repo.get_policy(policy_id)
        if current is None:
            raise KeyError(f"Policy '{policy_id}' not found.")

        if authorized_scopes is not None:
            if not any(
                self.is_policy_in_scope(
                    current,
                    application_id=getattr(s, "application_id", None)
                    if not isinstance(s, dict)
                    else s.get("application_id"),
                    environment=getattr(s, "environment", None)
                    if not isinstance(s, dict)
                    else s.get("environment"),
                )
                for s in authorized_scopes
            ):
                raise PermissionError(
                    f"Policy '{policy_id}' is outside caller's authorized scope."
                )

        self.validate_policy_safety(update)

        updated_policy = self.policy_repo.update_policy(
            policy_id=policy_id,
            update=update,
            actor=actor,
            description=description,
        )

        # If policy was deactivated (disabled), invalidate pending approvals
        if update.enabled is False and current.enabled is True:
            self.approval_service.invalidate_approvals_for_policy(
                policy_id, reason=f"Policy '{policy_id}' was deactivated."
            )

        return updated_policy

    def enable_policy(
        self,
        policy_id: str,
        actor: str | None = None,
        authorized_scopes: Sequence[Any] | None = None,
        description: str | None = None,
    ) -> PolicyRead:
        """Enables a policy through the existing supported update mechanism."""
        return self.update_policy(
            policy_id=policy_id,
            update=PolicyUpdate(enabled=True),
            actor=actor,
            authorized_scopes=authorized_scopes,
            description=description or f"Policy '{policy_id}' enabled.",
        )

    def disable_policy(
        self,
        policy_id: str,
        actor: str | None = None,
        authorized_scopes: Sequence[Any] | None = None,
        description: str | None = None,
    ) -> PolicyRead:
        """Disables a policy through the existing supported update mechanism."""
        return self.update_policy(
            policy_id=policy_id,
            update=PolicyUpdate(enabled=False),
            actor=actor,
            authorized_scopes=authorized_scopes,
            description=description or f"Policy '{policy_id}' disabled.",
        )

    def delete_policy(
        self,
        policy_id: str,
        actor: str | None = None,
        authorized_scopes: Sequence[Any] | None = None,
        description: str | None = None,
    ) -> bool:
        """Retires/deletes an existing policy under retention rules and invalidates pending approvals."""
        current = self.policy_repo.get_policy(policy_id)
        if current is None:
            raise KeyError(f"Policy '{policy_id}' not found.")

        if authorized_scopes is not None:
            if not any(
                self.is_policy_in_scope(
                    current,
                    application_id=getattr(s, "application_id", None)
                    if not isinstance(s, dict)
                    else s.get("application_id"),
                    environment=getattr(s, "environment", None)
                    if not isinstance(s, dict)
                    else s.get("environment"),
                )
                for s in authorized_scopes
            ):
                raise PermissionError(
                    f"Policy '{policy_id}' is outside caller's authorized scope."
                )

        result = self.policy_repo.delete_policy(
            policy_id, actor=actor, description=description
        )

        # Invalidate any pending approval requests tied to this deleted policy
        self.approval_service.invalidate_approvals_for_policy(
            policy_id,
            reason=f"Policy '{policy_id}' was deleted; approval invalidated.",
        )

        return result

    def rollback_policy(
        self,
        policy_id: str,
        target_version: int,
        actor: str | None = None,
        authorized_scopes: Sequence[Any] | None = None,
        description: str | None = None,
    ) -> PolicyRead:
        """Rolls back an active or retired policy to an earlier revision, creating a new monotonic version.

        Enforces:
        - Scope isolation: caller must be authorized for the policy's target scope.
        - Approval invalidation: invalidates pending approvals due to policy version change.
        """
        # Retrieve target revision or current policy to verify scope authorization
        target = self.policy_repo.get_policy(policy_id, version=target_version)
        if target is None:
            # Check if policy even exists
            revisions = self.policy_repo.get_policy_revisions(policy_id)
            if not revisions:
                raise KeyError(f"Policy '{policy_id}' not found.")
            raise KeyError(
                f"Historical revision {target_version} for policy '{policy_id}' not found."
            )

        if authorized_scopes is not None:
            if not any(
                self.is_policy_in_scope(
                    target,
                    application_id=getattr(s, "application_id", None)
                    if not isinstance(s, dict)
                    else s.get("application_id"),
                    environment=getattr(s, "environment", None)
                    if not isinstance(s, dict)
                    else s.get("environment"),
                )
                for s in authorized_scopes
            ):
                raise PermissionError(
                    f"Policy '{policy_id}' is outside caller's authorized scope."
                )

        new_policy = self.policy_repo.rollback_policy(
            policy_id=policy_id,
            target_version=target_version,
            actor=actor,
            description=description,
        )

        # Invalidate any pending approval requests tied to prior revisions
        self.approval_service.invalidate_approvals_for_policy(
            policy_id,
            reason=f"Policy '{policy_id}' was rolled back to revision {target_version}.",
        )

        return new_policy

    def simulate_policy(
        self,
        request: AuthorizationRequest,
        policy_id: str | None = None,
        candidate_policy: PolicyCreate | None = None,
        version: int | None = None,
        authorized_scopes: Sequence[Any] | None = None,
    ) -> PolicySimulationResponse:
        """Simulates policy evaluation against a hypothetical authorization request.

        Supports:
        - Simulating an existing persisted policy (by policy_id and optional version).
        - Simulating a draft/candidate policy (PolicyCreate) without saving to repository.
        - Strict scope enforcement: rejects with PermissionError if caller lacks authorization.
        - Safety validation: rejects candidate policies containing code execution or SQL injection.
        - Zero side effects: does not persist decisions or trigger approval workflows.
        """
        target_policy: PolicyCreate | PolicyRead
        target_version: int | None

        if candidate_policy is not None:
            # 1. Candidate policy safety validation
            self.validate_policy_safety(candidate_policy)

            # 2. Scope verification for candidate policy
            if authorized_scopes is not None:
                if not any(
                    self.is_policy_in_scope(
                        candidate_policy,
                        application_id=getattr(s, "application_id", None)
                        if not isinstance(s, dict)
                        else s.get("application_id"),
                        environment=getattr(s, "environment", None)
                        if not isinstance(s, dict)
                        else s.get("environment"),
                    )
                    for s in authorized_scopes
                ):
                    raise PermissionError(
                        f"Candidate policy target scope ({getattr(candidate_policy, 'application_id', None)}, {getattr(candidate_policy, 'environment', None)}) is outside caller's authorized scope."
                    )

            target_policy = candidate_policy
            target_version = None
        elif policy_id is not None:
            # 1. Retrieve existing policy
            if version is not None:
                policy_obj = self.policy_repo.get_policy(policy_id, version=version)
                if policy_obj is None:
                    raise KeyError(f"Policy '{policy_id}' revision {version} not found.")
            else:
                policy_obj = self.policy_repo.get_policy(policy_id)
                if policy_obj is None:
                    revisions = self.policy_repo.get_policy_revisions(policy_id)
                    if not revisions:
                        raise KeyError(f"Policy '{policy_id}' not found.")
                    policy_obj = revisions[-1]

            # 2. Scope verification
            if authorized_scopes is not None:
                if not any(
                    self.is_policy_in_scope(
                        policy_obj,
                        application_id=getattr(s, "application_id", None)
                        if not isinstance(s, dict)
                        else s.get("application_id"),
                        environment=getattr(s, "environment", None)
                        if not isinstance(s, dict)
                        else s.get("environment"),
                    )
                    for s in authorized_scopes
                ):
                    raise PermissionError(
                        f"Policy '{policy_id}' is outside caller's authorized application/environment scope."
                    )

            target_policy = policy_obj
            target_version = getattr(policy_obj, "version", version)
        else:
            # Active policy scope simulation
            active_policies = self.policy_repo.list_active_policies(
                action=request.action,
                resource=request.resource,
            )
            if authorized_scopes is not None:
                active_policies = [
                    p for p in active_policies
                    if any(
                        self.is_policy_in_scope(
                            p,
                            application_id=getattr(s, "application_id", None)
                            if not isinstance(s, dict)
                            else s.get("application_id"),
                            environment=getattr(s, "environment", None)
                            if not isinstance(s, dict)
                            else s.get("environment"),
                        )
                        for s in authorized_scopes
                    )
                ]

            from datetime import datetime, timezone
            from backend.app.policy_engine.evaluator import evaluate_policies
            eval_decision = evaluate_policies(active_policies, request)

            all_traces: list[RuleSimulationTrace] = []
            all_conflicts: list[ConflictDetectionResult] = []
            for p in active_policies:
                p_sim = simulate_policy_evaluation(p, request, policy_version=p.version)
                all_traces.extend(p_sim.rule_traces)
                all_conflicts.extend(p_sim.conflicts)

            return PolicySimulationResponse(
                policy_id=eval_decision.policy_id or "multi_policy_simulation",
                policy_version=None,
                decision=eval_decision.decision,
                matched_rule_id=eval_decision.matched_rule_id,
                matched_rule_ids=eval_decision.matched_rule_ids,
                reason_code=eval_decision.reason_code,
                reason_message=eval_decision.reason_message,
                missing_attributes=[],
                rule_traces=all_traces,
                conflicts=all_conflicts,
                simulated_at=datetime.now(timezone.utc),
            )

        return simulate_policy_evaluation(
            policy=target_policy,
            request=request,
            policy_version=target_version,
        )

    def detect_conflicts(
        self,
        policy_id: str | None = None,
        candidate_policy: PolicyCreate | None = None,
        version: int | None = None,
        authorized_scopes: Sequence[Any] | None = None,
    ) -> list[ConflictDetectionResult]:
        """Analyzes a policy for rule conflicts, shadows, and disabled rules."""
        if candidate_policy is not None:
            self.validate_policy_safety(candidate_policy)
            if authorized_scopes is not None:
                if not any(
                    self.is_policy_in_scope(
                        candidate_policy,
                        application_id=getattr(s, "application_id", None)
                        if not isinstance(s, dict)
                        else s.get("application_id"),
                        environment=getattr(s, "environment", None)
                        if not isinstance(s, dict)
                        else s.get("environment"),
                    )
                    for s in authorized_scopes
                ):
                    raise PermissionError(
                        f"Candidate policy target scope ({getattr(candidate_policy, 'application_id', None)}, {getattr(candidate_policy, 'environment', None)}) is outside caller's authorized scope."
                    )
            return detect_policy_conflicts(candidate_policy)

        if policy_id is None:
            raise ValueError("Either candidate_policy or policy_id must be provided.")

        if version is not None:
            policy_obj = self.policy_repo.get_policy(policy_id, version=version)
            if policy_obj is None:
                raise KeyError(f"Policy '{policy_id}' revision {version} not found.")
        else:
            policy_obj = self.policy_repo.get_policy(policy_id)
            if policy_obj is None:
                revisions = self.policy_repo.get_policy_revisions(policy_id)
                if not revisions:
                    raise KeyError(f"Policy '{policy_id}' not found.")
                policy_obj = revisions[-1]

        if authorized_scopes is not None:
            if not any(
                self.is_policy_in_scope(
                    policy_obj,
                    application_id=getattr(s, "application_id", None)
                    if not isinstance(s, dict)
                    else s.get("application_id"),
                    environment=getattr(s, "environment", None)
                    if not isinstance(s, dict)
                    else s.get("environment"),
                )
                for s in authorized_scopes
            ):
                raise PermissionError(
                    f"Policy '{policy_id}' is outside caller's authorized application/environment scope."
                )

        return detect_policy_conflicts(policy_obj)

