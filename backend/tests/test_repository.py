"""Unit tests for AMGR policy versioning, repository operations, and change history.

Validates initial policy revision creation, multiple sequential revisions,
immutability of historical revisions, retrieval of current vs historical revisions,
failed/rejected change isolation, audit trail recording (CREATED, UPDATED, ACTIVATED, DEACTIVATED),
and end-to-end integration with the policy evaluation engine.
"""

import unittest
from datetime import datetime, timezone

from backend.app.policy_engine.evaluator import evaluate_policies, evaluate_policy
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.authorization import AuthorizationRequest, ReasonCode
from backend.app.schemas.condition import (
    PYDANTIC_AVAILABLE,
    ConditionGroup,
    ConditionRule,
)
from backend.app.schemas.policy import (
    PolicyCreate,
    PolicyHistoryEntry,
    PolicyRead,
    PolicyUpdate,
    RuleDefinition,
)
from backend.app.schemas.policy_enums import (
    ConditionOperator,
    PolicyChangeType,
    PolicyEffect,
    RuleResolutionAlgorithm,
)

try:
    from pydantic import ValidationError
except ModuleNotFoundError:
    ValidationError = Exception  # type: ignore[assignment, misc]


@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestPolicyVersioning(unittest.TestCase):
    """Verifies monotonic version tracking, historical immutability, and retrieval."""

    def setUp(self) -> None:
        self.repo = PolicyRepository()
        self.initial_rule = RuleDefinition(
            id="rule_read_v1",
            name="Allow Read Access",
            effect=PolicyEffect.ALLOW,
            priority=1,
            condition=ConditionRule(
                field="action",
                operator=ConditionOperator.EQ,
                value="read_doc",
            ),
        )
        self.initial_policy = PolicyCreate(
            id="pol_docs",
            name="Document Access Policy",
            description="Initial policy revision",
            priority=10,
            rules=[self.initial_rule],
            default_effect=PolicyEffect.BLOCK,
        )

    def test_create_initial_policy_revision(self) -> None:
        """Creating a policy establishes revision 1 with timestamps and audit trail."""
        created = self.repo.create_policy(
            self.initial_policy,
            actor="sec_admin",
            description="Initial document policy release",
        )

        self.assertEqual(created.id, "pol_docs")
        self.assertEqual(created.version, 1)
        self.assertEqual(created.name, "Document Access Policy")
        self.assertTrue(created.enabled)
        self.assertIsNotNone(created.created_at)
        self.assertEqual(created.created_at, created.updated_at)

        # Retrieval checks
        current = self.repo.get_policy("pol_docs")
        self.assertIsNotNone(current)
        self.assertEqual(current.version, 1)  # type: ignore[union-attr]

        v1 = self.repo.get_policy("pol_docs", version=1)
        self.assertIsNotNone(v1)
        self.assertEqual(v1.version, 1)  # type: ignore[union-attr]

    def test_reject_duplicate_policy_creation(self) -> None:
        """Attempting to create a policy with an existing ID must be rejected."""
        self.repo.create_policy(self.initial_policy)
        with self.assertRaises(ValueError):
            self.repo.create_policy(self.initial_policy)

    def test_update_policy_creates_new_revision_and_retains_earlier(self) -> None:
        """Updating a policy creates revision 2 while keeping revision 1 intact and retrievable."""
        self.repo.create_policy(self.initial_policy)

        new_rule = RuleDefinition(
            id="rule_read_v2",
            name="Allow Read and Query",
            effect=PolicyEffect.ALLOW,
            priority=5,
            condition=ConditionRule(
                field="action",
                operator=ConditionOperator.IN,
                value=["read_doc", "query_doc"],
            ),
        )
        update = PolicyUpdate(
            name="Updated Document Policy",
            rules=[new_rule],
            priority=20,
        )

        updated = self.repo.update_policy(
            "pol_docs",
            update,
            actor="policy_team",
            description="Expanded document actions",
        )

        self.assertEqual(updated.version, 2)
        self.assertEqual(updated.name, "Updated Document Policy")
        self.assertEqual(updated.priority, 20)
        self.assertEqual(len(updated.rules), 1)
        self.assertEqual(updated.rules[0].id, "rule_read_v2")

        # Current revision is now v2
        current = self.repo.get_policy("pol_docs")
        self.assertEqual(current.version, 2)  # type: ignore[union-attr]
        self.assertEqual(current.priority, 20)  # type: ignore[union-attr]

        # Historical revision v1 remains completely preserved
        hist_v1 = self.repo.get_policy("pol_docs", version=1)
        self.assertIsNotNone(hist_v1)
        self.assertEqual(hist_v1.version, 1)  # type: ignore[union-attr]
        self.assertEqual(hist_v1.name, "Document Access Policy")  # type: ignore[union-attr]
        self.assertEqual(hist_v1.priority, 10)  # type: ignore[union-attr]
        self.assertEqual(hist_v1.rules[0].id, "rule_read_v1")  # type: ignore[union-attr]

        # Revisions list
        all_revs = self.repo.get_policy_revisions("pol_docs")
        self.assertEqual([r.version for r in all_revs], [1, 2])

    def test_multiple_sequential_updates(self) -> None:
        """Monotonic version numbers increment through multiple revisions."""
        self.repo.create_policy(self.initial_policy)

        # Revision 2: Update priority
        self.repo.update_policy("pol_docs", PolicyUpdate(priority=15))
        # Revision 3: Deactivate policy
        self.repo.update_policy("pol_docs", PolicyUpdate(enabled=False))
        # Revision 4: Reactivate and rename
        self.repo.update_policy("pol_docs", PolicyUpdate(enabled=True, name="Restored Docs"))

        current = self.repo.get_policy("pol_docs")
        self.assertEqual(current.version, 4)  # type: ignore[union-attr]
        self.assertEqual(current.name, "Restored Docs")  # type: ignore[union-attr]
        self.assertTrue(current.enabled)  # type: ignore[union-attr]

        # Verify revision 3 was disabled
        v3 = self.repo.get_policy("pol_docs", version=3)
        self.assertFalse(v3.enabled)  # type: ignore[union-attr]
        self.assertEqual(v3.version, 3)  # type: ignore[union-attr]

    def test_historical_policy_immutability(self) -> None:
        """Retrieved historical and current policy models cannot be mutated in place."""
        created = self.repo.create_policy(self.initial_policy)
        with self.assertRaises(ValidationError):
            created.version = 99  # type: ignore[misc]
        with self.assertRaises(ValidationError):
            created.priority = 500  # type: ignore[misc]

    def test_get_nonexistent_policy_or_version_returns_none(self) -> None:
        """Nonexistent policy ID or version returns None without error."""
        self.assertIsNone(self.repo.get_policy("pol_unknown"))
        self.repo.create_policy(self.initial_policy)
        self.assertIsNone(self.repo.get_policy("pol_docs", version=999))
        self.assertEqual(self.repo.get_policy_revisions("pol_unknown"), [])


@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestChangeHistoryAndFailedUpdateIsolation(unittest.TestCase):
    """Verifies audit trail integrity and transactional isolation of failed operations."""

    def setUp(self) -> None:
        self.repo = PolicyRepository()
        self.rule = RuleDefinition(
            id="rule_1",
            name="Rule 1",
            effect=PolicyEffect.ALLOW,
            condition=ConditionRule(field="action", operator=ConditionOperator.EQ, value="run"),
        )
        self.policy = PolicyCreate(
            id="pol_audit",
            name="Audit Test Policy",
            rules=[self.rule],
        )

    def test_audit_history_records_change_types(self) -> None:
        """History accurately records CREATED, UPDATED, DEACTIVATED, and ACTIVATED."""
        # 1. CREATED
        self.repo.create_policy(self.policy, actor="alice", description="Created policy")
        # 2. UPDATED
        self.repo.update_policy("pol_audit", PolicyUpdate(name="New Name"), actor="bob")
        # 3. DEACTIVATED
        self.repo.update_policy("pol_audit", PolicyUpdate(enabled=False), actor="charlie")
        # 4. ACTIVATED
        self.repo.update_policy("pol_audit", PolicyUpdate(enabled=True), actor="dan")

        history = self.repo.get_change_history("pol_audit")
        self.assertEqual(len(history), 4)

        self.assertEqual(history[0].change_type, PolicyChangeType.CREATED)
        self.assertEqual(history[0].version, 1)
        self.assertEqual(history[0].actor, "alice")
        self.assertEqual(history[0].description, "Created policy")

        self.assertEqual(history[1].change_type, PolicyChangeType.UPDATED)
        self.assertEqual(history[1].version, 2)
        self.assertEqual(history[1].actor, "bob")

        self.assertEqual(history[2].change_type, PolicyChangeType.DEACTIVATED)
        self.assertEqual(history[2].version, 3)
        self.assertEqual(history[2].actor, "charlie")

        self.assertEqual(history[3].change_type, PolicyChangeType.ACTIVATED)
        self.assertEqual(history[3].version, 4)
        self.assertEqual(history[3].actor, "dan")

    def test_failed_update_never_records_in_history_or_modifies_state(self) -> None:
        """Transactional safety: If update validation fails, no history entry is written and version is not bumped."""
        self.repo.create_policy(self.policy)
        initial_history_len = len(self.repo.get_change_history("pol_audit"))
        self.assertEqual(initial_history_len, 1)

        # 1. Attempt invalid update: empty rules list (violates min_length=1)
        with self.assertRaises(ValidationError):
            self.repo.update_policy("pol_audit", PolicyUpdate(rules=[]))  # type: ignore[arg-type]

        # 2. Attempt invalid update: duplicate rule IDs in update
        dup_rules = [
            self.rule,
            RuleDefinition(
                id="rule_1",  # duplicate ID
                name="Duplicate",
                effect=PolicyEffect.BLOCK,
                condition=ConditionRule(field="action", operator=ConditionOperator.EQ, value="run"),
            ),
        ]
        with self.assertRaises(ValidationError):
            self.repo.update_policy("pol_audit", PolicyUpdate(rules=dup_rules))

        # 3. Attempt invalid update: negative priority
        with self.assertRaises(ValidationError):
            self.repo.update_policy("pol_audit", PolicyUpdate(priority=-10))

        # 4. Attempt update on non-existent policy ID
        with self.assertRaises(KeyError):
            self.repo.update_policy("pol_missing", PolicyUpdate(name="Oops"))

        # Verify state is completely preserved
        current = self.repo.get_policy("pol_audit")
        self.assertEqual(current.version, 1)  # type: ignore[union-attr]

        # Verify history is completely unpolluted by the failed attempts
        after_history = self.repo.get_change_history("pol_audit")
        self.assertEqual(len(after_history), 1)
        self.assertEqual(after_history[0].version, 1)

    def test_actor_attribution_omitted_when_not_provided(self) -> None:
        """When actor is omitted, it remains None rather than fabricated."""
        self.repo.create_policy(self.policy)
        entry = self.repo.get_change_history("pol_audit")[0]
        self.assertIsNone(entry.actor)


@unittest.skipUnless(PYDANTIC_AVAILABLE, "Pydantic is not installed in the environment")
class TestRepositoryEvaluatorIntegration(unittest.TestCase):
    """Verifies that repository-managed policy versions integrate with the evaluator."""

    def setUp(self) -> None:
        self.repo = PolicyRepository()

    def test_evaluator_uses_current_version_across_updates(self) -> None:
        """Evaluator decision reflects the updated rules of the latest revision."""
        # v1: Allows only role=developer
        rule_v1 = RuleDefinition(
            id="rule_dev",
            name="Developer Rule",
            effect=PolicyEffect.ALLOW,
            condition=ConditionRule(field="subject.role", operator=ConditionOperator.EQ, value="developer"),
        )
        self.repo.create_policy(
            PolicyCreate(id="pol_role", name="Role Policy", rules=[rule_v1], default_effect=PolicyEffect.BLOCK)
        )

        req_qa = AuthorizationRequest(
            trace_id="trc_rep_1",
            subject={"role": "qa"},
            action="deploy",
            resource="stage",
        )

        # v1 blocks QA
        dec_v1 = evaluate_policy(self.repo.get_policy("pol_role"), req_qa)  # type: ignore[arg-type]
        self.assertEqual(dec_v1.decision, PolicyEffect.BLOCK)

        # Update to v2: Allows role=qa
        rule_v2 = RuleDefinition(
            id="rule_qa",
            name="QA Rule",
            effect=PolicyEffect.ALLOW,
            condition=ConditionRule(field="subject.role", operator=ConditionOperator.IN, value=["developer", "qa"]),
        )
        self.repo.update_policy("pol_role", PolicyUpdate(rules=[rule_v2]))

        # v2 allows QA
        dec_v2 = evaluate_policy(self.repo.get_policy("pol_role"), req_qa)  # type: ignore[arg-type]
        self.assertEqual(dec_v2.decision, PolicyEffect.ALLOW)
        self.assertEqual(dec_v2.policy_id, "pol_role")

        # Explicitly evaluating historical v1 still blocks QA!
        hist_v1 = self.repo.get_policy("pol_role", version=1)
        dec_hist = evaluate_policy(hist_v1, req_qa)  # type: ignore[arg-type]
        self.assertEqual(dec_hist.decision, PolicyEffect.BLOCK)

    def test_multi_policy_evaluation_with_list_active_policies(self) -> None:
        """list_active_policies feeds directly into evaluate_policies with correct priority resolution."""
        # Pol A: priority 10, ALLOW
        pol_a = PolicyCreate(
            id="pol_a",
            name="Policy A",
            priority=10,
            rules=[
                RuleDefinition(
                    id="rule_a",
                    name="Rule A",
                    effect=PolicyEffect.ALLOW,
                    condition=ConditionRule(field="action", operator=ConditionOperator.EQ, value="exec"),
                )
            ],
        )
        # Pol B: priority 50, BLOCK
        pol_b = PolicyCreate(
            id="pol_b",
            name="Policy B",
            priority=50,
            rules=[
                RuleDefinition(
                    id="rule_b",
                    name="Rule B",
                    effect=PolicyEffect.BLOCK,
                    condition=ConditionRule(field="action", operator=ConditionOperator.EQ, value="exec"),
                )
            ],
        )

        self.repo.create_policy(pol_a)
        self.repo.create_policy(pol_b)

        req = AuthorizationRequest(
            trace_id="trc_rep_2",
            subject="user",
            action="exec",
            resource="tool",
        )

        active_pols = self.repo.list_active_policies()
        decision = evaluate_policies(active_pols, req)
        # Priority 50 (BLOCK) wins over priority 10 (ALLOW)
        self.assertEqual(decision.decision, PolicyEffect.BLOCK)
        self.assertEqual(decision.policy_id, "pol_b")

        # Deactivate Pol B via update
        self.repo.update_policy("pol_b", PolicyUpdate(enabled=False))

        # Now only Pol A is active
        active_pols_after = self.repo.list_active_policies()
        decision_after = evaluate_policies(active_pols_after, req)
        self.assertEqual(decision_after.decision, PolicyEffect.ALLOW)
        self.assertEqual(decision_after.policy_id, "pol_a")

    def test_missing_context_fails_closed_through_repository_policies(self) -> None:
        """Missing security context evaluated against repository policies never silently becomes ALLOW."""
        pol_clearance = PolicyCreate(
            id="pol_clearance",
            name="Clearance Required",
            priority=10,
            rules=[
                RuleDefinition(
                    id="rule_clearance",
                    name="Rule Clearance",
                    effect=PolicyEffect.ALLOW,
                    condition=ConditionRule(
                        field="subject.security_level",
                        operator=ConditionOperator.GTE,
                        value=4,
                    ),
                )
            ],
        )
        self.repo.create_policy(pol_clearance)

        # Request missing 'security_level'
        req_missing = AuthorizationRequest(
            trace_id="trc_rep_3",
            subject={"username": "unauthenticated"},
            action="access",
            resource="vault",
        )

        active = self.repo.list_active_policies()
        decision = evaluate_policies(active, req_missing)
        self.assertEqual(decision.decision, PolicyEffect.BLOCK)
        self.assertEqual(decision.reason_code, ReasonCode.MISSING_CONTEXT)


if __name__ == "__main__":
    unittest.main()
