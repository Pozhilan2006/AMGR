"""Policy versioning and change history repository for AMGR.

Implements deterministic policy revision management, immutability of historical
definitions, and audit trail tracking for policy updates following the Member 3 roadmap.
Supports both SQLAlchemy database persistence and lightweight in-memory operation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.policy import PolicyModel
from backend.app.models.policy_version import PolicyVersionModel
from backend.app.schemas.policy import (
    PolicyCreate,
    PolicyHistoryEntry,
    PolicyRead,
    PolicyUpdate,
    RuleDefinition,
    sort_policies,
)
from backend.app.schemas.policy_enums import (
    PolicyChangeType,
    PolicyEffect,
    RuleResolutionAlgorithm,
)


class PolicyPersistenceError(RuntimeError):
    """Raised when a database persistence operation fails and is safely rolled back."""
    pass


class PolicyRepository:
    """Policy versioning and change history repository.

    Provides:
    - Stable policy identification and monotonic revision tracking (v1, v2, ...).
    - Immutable historical revisions preserved across updates.
    - Explicit retrieval of current or specific historical revisions.
    - Audit logging of successful changes with timestamps and optional actor attribution.
    - Transactional isolation: failed or rejected updates leave state and history unaltered.
    - Dual persistence support: operates against SQLAlchemy database sessions when provided,
      with full in-memory fallback for test isolation.
    """

    def __init__(self, db_session: Session | None = None) -> None:
        self.db_session = db_session
        # Maps policy_id -> {version_number: PolicyRead}
        self._revisions: dict[str, dict[int, PolicyRead]] = {}
        # Maps policy_id -> latest_version_number
        self._current_versions: dict[str, int] = {}
        # Append-only change audit log
        self._history: list[PolicyHistoryEntry] = []

        if self.db_session is not None:
            self._load_from_db()

    def _load_from_db(self) -> None:
        """Hydrates repository state from the database session."""
        if self.db_session is None:
            return

        stmt_policies = select(PolicyModel)
        db_policies = self.db_session.scalars(stmt_policies).all()

        for p_model in db_policies:
            # Load versions for this policy
            stmt_versions = (
                select(PolicyVersionModel)
                .where(PolicyVersionModel.policy_id == p_model.id)
                .order_by(PolicyVersionModel.version.asc())
            )
            db_versions = self.db_session.scalars(stmt_versions).all()

            rev_dict: dict[int, PolicyRead] = {}
            for v_model in db_versions:
                rules = [RuleDefinition.model_validate(r) for r in v_model.rules_data]
                read_model = PolicyRead(
                    id=p_model.id,
                    name=p_model.name,
                    description=p_model.description,
                    priority=p_model.priority,
                    rules=rules,
                    resolution_algorithm=RuleResolutionAlgorithm(p_model.resolution_algorithm),
                    default_effect=PolicyEffect(p_model.default_effect),
                    enabled=p_model.enabled,
                    version=v_model.version,
                    created_at=p_model.created_at,
                    updated_at=v_model.timestamp,
                )
                rev_dict[v_model.version] = read_model

                entry = PolicyHistoryEntry(
                    policy_id=p_model.id,
                    version=v_model.version,
                    change_type=PolicyChangeType(v_model.change_type),
                    timestamp=v_model.timestamp,
                    actor=v_model.actor,
                    description=v_model.description or f"Policy '{p_model.id}' version {v_model.version}.",
                )
                self._history.append(entry)

            if rev_dict:
                self._revisions[p_model.id] = rev_dict
                self._current_versions[p_model.id] = p_model.version
            else:
                rules = [RuleDefinition.model_validate(r) for r in p_model.rules_data]
                current_read = PolicyRead(
                    id=p_model.id,
                    name=p_model.name,
                    description=p_model.description,
                    priority=p_model.priority,
                    rules=rules,
                    resolution_algorithm=RuleResolutionAlgorithm(p_model.resolution_algorithm),
                    default_effect=PolicyEffect(p_model.default_effect),
                    enabled=p_model.enabled,
                    version=p_model.version,
                    created_at=p_model.created_at,
                    updated_at=p_model.updated_at,
                )
                self._revisions[p_model.id] = {p_model.version: current_read}
                self._current_versions[p_model.id] = p_model.version

        self._history.sort(key=lambda e: (e.timestamp, e.policy_id, e.version))

    def create_policy(
        self,
        policy: PolicyCreate,
        actor: str | None = None,
        description: str | None = None,
    ) -> PolicyRead:
        """Creates the initial revision (v1) of a new policy.

        Raises:
            ValueError: If a policy with the given ID already exists.
            PolicyPersistenceError: If database persistence fails (transaction is rolled back).
        """
        if policy.id in self._current_versions:
            raise ValueError(
                f"Policy with ID '{policy.id}' already exists. Use update_policy to create a new revision."
            )

        now = datetime.now(timezone.utc)
        policy_read = PolicyRead(
            id=policy.id,
            name=policy.name,
            description=policy.description,
            priority=policy.priority,
            rules=policy.rules,
            resolution_algorithm=policy.resolution_algorithm,
            default_effect=policy.default_effect,
            enabled=policy.enabled,
            version=1,
            created_at=now,
            updated_at=now,
        )

        entry = PolicyHistoryEntry(
            policy_id=policy.id,
            version=1,
            change_type=PolicyChangeType.CREATED,
            timestamp=now,
            actor=actor,
            description=description or f"Policy '{policy.id}' created (revision 1).",
        )

        # Database persistence with transactional isolation
        if self.db_session is not None:
            try:
                rules_dump = [r.model_dump(mode="json") for r in policy.rules]
                db_policy = PolicyModel(
                    id=policy.id,
                    name=policy.name,
                    description=policy.description,
                    priority=policy.priority,
                    resolution_algorithm=policy.resolution_algorithm.value,
                    default_effect=policy.default_effect.value,
                    enabled=policy.enabled,
                    version=1,
                    rules_data=rules_dump,
                    created_at=now,
                    updated_at=now,
                )
                db_version = PolicyVersionModel(
                    policy_id=policy.id,
                    version=1,
                    change_type=PolicyChangeType.CREATED.value,
                    actor=actor,
                    description=description or f"Policy '{policy.id}' created (revision 1).",
                    rules_data=rules_dump,
                    timestamp=now,
                )
                self.db_session.add(db_policy)
                self.db_session.add(db_version)
                self.db_session.commit()
            except Exception as exc:
                self.db_session.rollback()
                raise PolicyPersistenceError(
                    f"Failed to persist policy '{policy.id}' to database: {exc}"
                ) from exc

        # Mutate in-memory state only upon successful persistence
        self._revisions[policy.id] = {1: policy_read}
        self._current_versions[policy.id] = 1
        self._history.append(entry)

        return policy_read

    def update_policy(
        self,
        policy_id: str,
        update: PolicyUpdate,
        actor: str | None = None,
        description: str | None = None,
    ) -> PolicyRead:
        """Updates an existing policy, creating an immutable new revision.

        Preserves all earlier revisions in history.
        If validation fails, state and change history remain completely untouched.

        Raises:
            KeyError: If the policy does not exist.
            ValueError: If the update introduces invalid configuration (e.g. duplicate rule IDs).
            PolicyPersistenceError: If database persistence fails (transaction is rolled back).
        """
        if policy_id not in self._current_versions:
            raise KeyError(f"Policy '{policy_id}' not found.")

        current_ver = self._current_versions[policy_id]
        current = self._revisions[policy_id][current_ver]

        # Merge updated attributes with current attributes
        new_name = update.name if update.name is not None else current.name
        new_description = (
            update.description if update.description is not None else current.description
        )
        new_priority = (
            update.priority if update.priority is not None else current.priority
        )
        new_rules = update.rules if update.rules is not None else current.rules
        new_resolution = (
            update.resolution_algorithm
            if update.resolution_algorithm is not None
            else current.resolution_algorithm
        )
        new_default_effect = (
            update.default_effect
            if update.default_effect is not None
            else current.default_effect
        )
        new_enabled = (
            update.enabled if update.enabled is not None else current.enabled
        )

        new_version = current_ver + 1
        now = datetime.now(timezone.utc)

        # Full validation via PolicyRead instantiation (enforces all invariants)
        new_policy_read = PolicyRead(
            id=policy_id,
            name=new_name,
            description=new_description,
            priority=new_priority,
            rules=new_rules,
            resolution_algorithm=new_resolution,
            default_effect=new_default_effect,
            enabled=new_enabled,
            version=new_version,
            created_at=current.created_at,
            updated_at=now,
        )

        # Determine change classification
        if update.enabled is not None and update.enabled != current.enabled:
            change_type = (
                PolicyChangeType.ACTIVATED
                if update.enabled
                else PolicyChangeType.DEACTIVATED
            )
        else:
            change_type = PolicyChangeType.UPDATED

        entry = PolicyHistoryEntry(
            policy_id=policy_id,
            version=new_version,
            change_type=change_type,
            timestamp=now,
            actor=actor,
            description=description
            or f"Policy '{policy_id}' updated to revision {new_version}.",
        )

        # Database persistence with transactional isolation
        if self.db_session is not None:
            try:
                db_policy = self.db_session.get(PolicyModel, policy_id)
                if db_policy is not None:
                    rules_dump = [r.model_dump(mode="json") for r in new_rules]
                    db_policy.name = new_name
                    db_policy.description = new_description
                    db_policy.priority = new_priority
                    db_policy.resolution_algorithm = new_resolution.value
                    db_policy.default_effect = new_default_effect.value
                    db_policy.enabled = new_enabled
                    db_policy.version = new_version
                    db_policy.rules_data = rules_dump
                    db_policy.updated_at = now

                    db_version = PolicyVersionModel(
                        policy_id=policy_id,
                        version=new_version,
                        change_type=change_type.value,
                        actor=actor,
                        description=description
                        or f"Policy '{policy_id}' updated to revision {new_version}.",
                        rules_data=rules_dump,
                        timestamp=now,
                    )
                    self.db_session.add(db_version)
                    self.db_session.commit()
            except Exception as exc:
                self.db_session.rollback()
                raise PolicyPersistenceError(
                    f"Failed to persist update for policy '{policy_id}' to database: {exc}"
                ) from exc

        # Commit new revision atomically to in-memory state only upon successful persistence
        self._revisions[policy_id][new_version] = new_policy_read
        self._current_versions[policy_id] = new_version
        self._history.append(entry)

        return new_policy_read

    def delete_policy(
        self,
        policy_id: str,
        actor: str | None = None,
        description: str | None = None,
    ) -> bool:
        """Retires/deletes an existing policy from current active definitions.

        Removes the policy from current versions while preserving revision history
        and recording an audit history entry for compliance and drift tracking.

        Raises:
            KeyError: If the policy does not exist.
            PolicyPersistenceError: If database persistence fails (transaction is rolled back).
        """
        if policy_id not in self._current_versions:
            raise KeyError(f"Policy '{policy_id}' not found.")

        current_ver = self._current_versions[policy_id]
        now = datetime.now(timezone.utc)

        entry = PolicyHistoryEntry(
            policy_id=policy_id,
            version=current_ver,
            change_type=PolicyChangeType.DEACTIVATED,
            timestamp=now,
            actor=actor,
            description=description or f"Policy '{policy_id}' retired and deleted from active policies.",
        )

        # Database persistence with transactional isolation
        if self.db_session is not None:
            try:
                db_policy = self.db_session.get(PolicyModel, policy_id)
                if db_policy is not None:
                    self.db_session.delete(db_policy)
                    self.db_session.commit()
            except Exception as exc:
                self.db_session.rollback()
                raise PolicyPersistenceError(
                    f"Failed to delete policy '{policy_id}' from database: {exc}"
                ) from exc

        # Pop from current versions and record history entry only upon successful DB transaction
        self._current_versions.pop(policy_id, None)
        self._history.append(entry)

        return True

    def rollback_policy(
        self,
        policy_id: str,
        target_version: int,
        actor: str | None = None,
        description: str | None = None,
    ) -> PolicyRead:
        """Rolls back an active or retired policy to a historical revision.

        Creates a new monotonic revision copying the definition from target_version,
        preserving history immutability and recording a PolicyChangeType.ROLLED_BACK entry.

        Raises:
            KeyError: If policy_id or target_version does not exist.
            ValueError: If target_version is already the current active revision.
            PolicyPersistenceError: If database persistence fails (transaction is rolled back).
        """
        if policy_id not in self._revisions or not self._revisions[policy_id]:
            raise KeyError(f"Policy '{policy_id}' not found.")

        if target_version not in self._revisions[policy_id]:
            raise KeyError(
                f"Historical revision {target_version} for policy '{policy_id}' not found."
            )

        target = self._revisions[policy_id][target_version]
        is_active = policy_id in self._current_versions

        if is_active:
            current_ver = self._current_versions[policy_id]
            if target_version == current_ver:
                raise ValueError(
                    f"Policy '{policy_id}' is already at revision {target_version}; cannot rollback to current active revision."
                )
            new_version = current_ver + 1
        else:
            latest_rev = max(self._revisions[policy_id].keys())
            new_version = latest_rev + 1

        now = datetime.now(timezone.utc)
        new_policy = PolicyRead(
            id=policy_id,
            name=target.name,
            description=target.description,
            priority=target.priority,
            rules=target.rules,
            resolution_algorithm=target.resolution_algorithm,
            default_effect=target.default_effect,
            enabled=target.enabled,
            version=new_version,
            created_at=target.created_at,
            updated_at=now,
        )

        rollback_desc = (
            description
            or f"Rolled back policy '{policy_id}' to revision {target_version}."
        )
        entry = PolicyHistoryEntry(
            policy_id=policy_id,
            version=new_version,
            change_type=PolicyChangeType.ROLLED_BACK,
            timestamp=now,
            actor=actor,
            description=rollback_desc,
            metadata={
                "rolled_back_from_version": self._current_versions.get(policy_id),
                "rolled_back_to_version": target_version,
                "was_deleted": not is_active,
            },
        )

        # Database persistence with transactional isolation
        if self.db_session is not None:
            try:
                db_policy = self.db_session.get(PolicyModel, policy_id)
                rules_dump = [r.model_dump(mode="json") for r in target.rules]
                if db_policy is not None:
                    db_policy.name = target.name
                    db_policy.description = target.description
                    db_policy.priority = target.priority
                    db_policy.resolution_algorithm = target.resolution_algorithm.value
                    db_policy.default_effect = target.default_effect.value
                    db_policy.enabled = target.enabled
                    db_policy.version = new_version
                    db_policy.rules_data = rules_dump
                    db_policy.updated_at = now
                else:
                    # Restore deleted policy to active table
                    db_policy = PolicyModel(
                        id=policy_id,
                        name=target.name,
                        description=target.description,
                        priority=target.priority,
                        resolution_algorithm=target.resolution_algorithm.value,
                        default_effect=target.default_effect.value,
                        enabled=target.enabled,
                        version=new_version,
                        rules_data=rules_dump,
                        created_at=target.created_at,
                        updated_at=now,
                    )
                    self.db_session.add(db_policy)

                db_version = PolicyVersionModel(
                    policy_id=policy_id,
                    version=new_version,
                    change_type=PolicyChangeType.ROLLED_BACK.value,
                    actor=actor,
                    description=rollback_desc,
                    rules_data=rules_dump,
                    timestamp=now,
                )
                self.db_session.add(db_version)
                self.db_session.commit()
            except Exception as exc:
                self.db_session.rollback()
                raise PolicyPersistenceError(
                    f"Failed to persist rollback for policy '{policy_id}' to database: {exc}"
                ) from exc

        # Atomically update in-memory state only upon successful persistence
        self._revisions[policy_id][new_version] = new_policy
        self._current_versions[policy_id] = new_version
        self._history.append(entry)

        return new_policy

    def get_policy(
        self, policy_id: str, version: int | None = None
    ) -> PolicyRead | None:
        """Retrieves a policy by ID, either latest (default) or a specific historical revision.

        Returns None if the policy or requested version does not exist.
        """
        if version is None:
            if policy_id not in self._current_versions:
                return None
            latest_ver = self._current_versions[policy_id]
            return self._revisions[policy_id][latest_ver]

        if policy_id not in self._revisions:
            return None
        return self._revisions[policy_id].get(version)

    def get_policy_revisions(self, policy_id: str) -> list[PolicyRead]:
        """Returns all historical revisions for a policy, sorted by version ascending."""
        if policy_id not in self._revisions:
            return []
        return [
            self._revisions[policy_id][v]
            for v in sorted(self._revisions[policy_id].keys())
        ]

    def get_change_history(
        self, policy_id: str | None = None
    ) -> list[PolicyHistoryEntry]:
        """Returns change history entries, optionally filtered by policy ID, in consistent chronological order."""
        if policy_id is None:
            return sorted(self._history, key=lambda e: (e.timestamp, e.policy_id, e.version))
        entries = [e for e in self._history if e.policy_id == policy_id]
        return sorted(entries, key=lambda e: (e.version, e.timestamp))

    @staticmethod
    def _is_policy_applicable(
        policy: PolicyRead,
        action: str | None = None,
        resource: str | None = None,
    ) -> bool:
        """Determines whether an enabled policy could apply to the given action and resource.

        Semantic rules:
        - If both action and resource are None, the policy is applicable.
        - A policy is applicable if ANY of its enabled rules could apply.
        - For an enabled rule:
          - target_actions=None means unrestricted for all actions.
          - target_resources=None means unrestricted for all resources.
          - If target_actions is a list, the requested action must be in that list.
          - If target_resources is a list, the requested resource must be in that list.
          - Empty target lists ([]) never match any action or resource.
        """
        if action is None and resource is None:
            return True

        for rule in policy.rules:
            if not rule.enabled:
                continue

            # Check action scoping filter
            if action is not None and rule.target_actions is not None:
                if action not in rule.target_actions:
                    continue

            # Check resource scoping filter
            if resource is not None and rule.target_resources is not None:
                if resource not in rule.target_resources:
                    continue

            # This enabled rule is compatible with (or unrestricted for) action and resource
            return True

        return False

    def list_active_policies(
        self,
        action: str | None = None,
        resource: str | None = None,
    ) -> list[PolicyRead]:
        """Returns the current revision of all enabled policies, optionally filtered by action and resource.

        Policies are sorted primarily by priority descending, with ties broken by policy ID ascending.
        A policy is included if it is enabled and at least one enabled rule can apply to the
        specified action and resource (or if action and resource are unrestricted).
        """
        active: list[PolicyRead] = []
        for p_id, ver in self._current_versions.items():
            policy = self._revisions[p_id][ver]
            if not policy.enabled:
                continue
            if self._is_policy_applicable(policy, action=action, resource=resource):
                active.append(policy)
        return sort_policies(active)

    def list_all_policies(self) -> list[PolicyRead]:
        """Returns the current revision of all policies, sorted by priority."""
        all_pols = [
            self._revisions[p_id][ver]
            for p_id, ver in self._current_versions.items()
        ]
        return sort_policies(all_pols)
