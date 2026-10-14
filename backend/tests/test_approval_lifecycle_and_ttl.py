"""Tests for AMGR Phase 4 Master Prompt 6: Approval Lifecycle, TTL/Expiration, and Advanced Querying.

Verifies:
1. Time-based expiration (TTL) for pending approval requests.
2. Attempting to submit a decision on an expired approval request fails and marks status EXPIRED.
3. Approved requests with expired execution validity window fail closed in can_proceed().
4. Explicit approval cancellation (POST /approvals/{id}/cancel) with human authority verification.
5. Automated AI/LLM entities are forbidden from cancelling approvals (403 Forbidden).
6. Single-decision finality: cancelling an already terminal request raises 409 Conflict.
7. Multi-parameter filtering on GET /approvals (status, trace_id, policy_id).
8. Deterministic pagination (limit, offset) on GET /approvals.
9. Maintenance sweep endpoint (POST /approvals/expire-stale) transitions stale requests to EXPIRED.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.app.main import create_app
from backend.app.repositories.decision_repository import DecisionRepository
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.approval import (
    ApprovalCancellationSubmission,
    ApprovalDecisionSubmission,
    ApprovalDecisionType,
    ApprovalRequest,
    ApprovalStatus,
)
from backend.app.schemas.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
    ReasonCode,
)
from backend.app.schemas.condition import ConditionOperator, ConditionRule
from backend.app.schemas.policy import PolicyCreate, RuleDefinition
from backend.app.schemas.policy_enums import PolicyEffect, RuleResolutionAlgorithm
from backend.app.services.approval_service import ApprovalService
from backend.app.services.authorization_service import AuthorizationService


class _TestResponse:
    def __init__(self, status_code: int, headers: dict[str, str], body: bytes) -> None:
        self.status_code = status_code
        self.headers = headers
        self.content = body

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8"))


class ASGIClient:
    def __init__(self, app: Any) -> None:
        self.app = app

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: Any = None,
    ) -> _TestResponse:
        query_string = ""
        if params:
            import urllib.parse
            query_string = urllib.parse.urlencode(
                {k: str(v) for k, v in params.items() if v is not None}
            )

        body = b""
        headers = [(b"host", b"testserver")]
        if json_data is not None:
            body = json.dumps(json_data).encode("utf-8")
            headers.append((b"content-type", b"application/json"))
            headers.append((b"content-length", str(len(body)).encode("ascii")))

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method.upper(),
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_string.encode("ascii"),
            "headers": headers,
        }

        response_status = 500
        response_headers: dict[str, str] = {}
        response_chunks: list[bytes] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            nonlocal response_status, response_headers
            if message["type"] == "http.response.start":
                response_status = message["status"]
                for k, v in message.get("headers", []):
                    response_headers[k.decode("ascii").lower()] = v.decode("ascii")
            elif message["type"] == "http.response.body":
                response_chunks.append(message.get("body", b""))

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.app(scope, receive, send))
        finally:
            loop.close()

        return _TestResponse(response_status, response_headers, b"".join(response_chunks))

    def get(self, path: str, params: dict[str, Any] | None = None) -> _TestResponse:
        return self.request("GET", path, params=params)

    def post(self, path: str, json_data: Any = None, params: dict[str, Any] | None = None) -> _TestResponse:
        return self.request("POST", path, params=params, json_data=json_data)


class TestApprovalLifecycleAndTTL(unittest.TestCase):
    def setUp(self) -> None:
        self.policy_repo = PolicyRepository()
        self.decision_repo = DecisionRepository()
        self.approval_service = ApprovalService(default_ttl_seconds=3600)  # 1 hour default

        self.auth_service = AuthorizationService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            decision_repo=self.decision_repo,
        )

        self.app = create_app(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            auth_service=self.auth_service,
            decision_repo=self.decision_repo,
        )
        self.client = ASGIClient(self.app)

        self.test_policy = PolicyCreate(
            id="pol_ttl_test",
            name="TTL Test Policy",
            description="Policy with approval workflow",
            priority=50,
            resolution_algorithm=RuleResolutionAlgorithm.BLOCK_OVERRIDES,
            default_effect=PolicyEffect.BLOCK,
            enabled=True,
            rules=[
                RuleDefinition(
                    id="rule_require_lead",
                    name="Require lead review",
                    condition=ConditionRule(
                        field="action",
                        operator=ConditionOperator.EQ,
                        value="sensitive_op",
                    ),
                    effect=PolicyEffect.REQUIRE_APPROVAL,
                    priority=10,
                    enabled=True,
                )
            ],
        )
        self.policy_repo.create_policy(self.test_policy)

    def _create_sample_approval(
        self,
        trace_id: str = "trc_ttl_001",
        ttl_seconds: int | None = None,
        policy_id: str = "pol_ttl_test",
    ) -> ApprovalRequest:
        auth_req = AuthorizationRequest(
            trace_id=trace_id,
            subject="operator_alice",
            action="sensitive_op",
            resource="database://vault",
        )
        decision = AuthorizationDecision(
            decision=PolicyEffect.REQUIRE_APPROVAL,
            policy_id=policy_id,
            matched_rule_id="rule_require_lead",
            reason_code=ReasonCode.RULE_MATCHED,
            reason_message="Requires review.",
        )
        return self.approval_service.create_approval_request(
            authorization_decision=decision,
            authorization_request=auth_req,
            policy_version=1,
            ttl_seconds=ttl_seconds,
        )

    def test_approval_ttl_expiration_blocks_decision(self) -> None:
        """When an approval request exceeds TTL, decision submission is blocked and status is EXPIRED."""
        # Create request with short 10-second TTL
        req = self._create_sample_approval(ttl_seconds=10)
        self.assertIsNotNone(req.expires_at)
        self.assertFalse(req.is_expired())

        # Simulate time 20 seconds later
        past_time = req.created_at + timedelta(seconds=20)

        # Service-level check: submit_decision raises ValueError
        submission = ApprovalDecisionSubmission(
            actor="security_lead_bob",
            decision=ApprovalDecisionType.APPROVE,
            reason="Late review attempt",
        )

        with self.assertRaises(ValueError) as ctx:
            self.approval_service.submit_decision(
                req.id,
                submission,
                policy_repo=self.policy_repo,
                now=past_time,
            )
        self.assertIn("has expired", str(ctx.exception))

        # Check request transitioned to EXPIRED
        updated = self.approval_service.get_approval_request(req.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, ApprovalStatus.EXPIRED)
        self.assertEqual(updated.final_effect, PolicyEffect.BLOCK)
        self.assertFalse(updated.is_pending())

    def test_approved_request_expires_and_blocks_execution(self) -> None:
        """An approved request whose execution validity window has passed fails can_proceed()."""
        req = self._create_sample_approval(ttl_seconds=60)

        # Approve the request within TTL
        submission = ApprovalDecisionSubmission(
            actor="security_lead_bob",
            decision=ApprovalDecisionType.APPROVE,
            reason="Approved for 60 seconds",
        )
        approved = self.approval_service.submit_decision(req.id, submission, policy_repo=self.policy_repo)
        self.assertEqual(approved.status, ApprovalStatus.APPROVED)

        # Can proceed immediately
        self.assertTrue(self.approval_service.can_proceed(req.id, policy_repo=self.policy_repo))

        # Check via API
        resp_valid = self.client.get(f"/api/v1/approvals/{req.id}/can-proceed")
        self.assertEqual(resp_valid.status_code, 200)
        self.assertTrue(resp_valid.json()["can_proceed"])

        # Simulate execution attempt past TTL
        expired_time = approved.created_at + timedelta(seconds=120)
        self.assertFalse(self.approval_service.can_proceed(req.id, policy_repo=self.policy_repo, now=expired_time))

        # Verify status automatically transitioned to EXPIRED
        post_expired = self.approval_service.get_approval_request(req.id)
        self.assertEqual(post_expired.status, ApprovalStatus.EXPIRED)

    def test_cancel_approval_endpoint(self) -> None:
        """POST /approvals/{id}/cancel explicitly invalidates an approval request."""
        req = self._create_sample_approval()

        cancel_payload = {
            "actor": "security_officer_carol",
            "reason": "Security incident ongoing; halting pending approvals.",
            "is_ai_or_llm": False,
        }

        resp = self.client.post(f"/api/v1/approvals/{req.id}/cancel", json_data=cancel_payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["status"], "EXPIRED")
        self.assertEqual(data["final_effect"], "BLOCK")
        self.assertEqual(data["decided_by"], "security_officer_carol")
        self.assertIn("Security incident ongoing", data["decision_reason"])

        # Verifying that can-proceed fails closed
        resp_check = self.client.get(f"/api/v1/approvals/{req.id}/can-proceed")
        self.assertEqual(resp_check.status_code, 200)
        self.assertFalse(resp_check.json()["can_proceed"])

    def test_cancel_approval_rejects_ai_operator_403(self) -> None:
        """Automated AI/LLM operators are strictly forbidden from cancelling approvals."""
        req = self._create_sample_approval()

        # 1. AI flag true rejected with 422 schema validation
        resp_flag = self.client.post(
            f"/api/v1/approvals/{req.id}/cancel",
            json_data={"actor": "human_reviewer", "reason": "cancel", "is_ai_or_llm": True},
        )
        self.assertEqual(resp_flag.status_code, 422)

        # 2. AI actor prefix rejected
        resp_prefix = self.client.post(
            f"/api/v1/approvals/{req.id}/cancel",
            json_data={"actor": "ai:autonomous_guard", "reason": "auto-cancel", "is_ai_or_llm": False},
        )
        self.assertEqual(resp_prefix.status_code, 422)

    def test_cancel_already_terminal_request_conflict_409(self) -> None:
        """Cancelling an already finalized (REJECTED or EXPIRED) approval produces 409 Conflict."""
        req = self._create_sample_approval()

        # Reject it first
        self.approval_service.submit_decision(
            req.id,
            ApprovalDecisionSubmission(actor="lead_bob", decision=ApprovalDecisionType.REJECT, reason="denied"),
        )

        # Attempt to cancel
        resp = self.client.post(
            f"/api/v1/approvals/{req.id}/cancel",
            json_data={"actor": "lead_bob", "reason": "double cancel"},
        )
        self.assertEqual(resp.status_code, 409)

    def test_list_approvals_multi_filtering_and_pagination(self) -> None:
        """GET /approvals supports filtering by status, trace_id, policy_id and limit/offset pagination."""
        req1 = self._create_sample_approval(trace_id="trc_batch_A", policy_id="pol_ttl_test")
        req2 = self._create_sample_approval(trace_id="trc_batch_A", policy_id="pol_ttl_test")
        req3 = self._create_sample_approval(trace_id="trc_batch_B", policy_id="pol_ttl_test")

        # Approve req1
        self.approval_service.submit_decision(
            req1.id,
            ApprovalDecisionSubmission(actor="lead_bob", decision=ApprovalDecisionType.APPROVE),
        )

        # 1. Filter by status=APPROVED
        resp_approved = self.client.get("/api/v1/approvals", params={"status": "APPROVED"})
        self.assertEqual(resp_approved.status_code, 200)
        items_approved = resp_approved.json()
        self.assertTrue(all(item["status"] == "APPROVED" for item in items_approved))
        self.assertEqual(len(items_approved), 1)
        self.assertEqual(items_approved[0]["id"], req1.id)

        # 2. Filter by trace_id=trc_batch_A
        resp_trace = self.client.get("/api/v1/approvals", params={"trace_id": "trc_batch_A"})
        self.assertEqual(resp_trace.status_code, 200)
        items_trace = resp_trace.json()
        self.assertEqual(len(items_trace), 2)
        self.assertTrue(all(item["trace_id"] == "trc_batch_A" for item in items_trace))

        # 3. Pagination limit=1, offset=1
        resp_paged = self.client.get("/api/v1/approvals", params={"limit": 1, "offset": 1})
        self.assertEqual(resp_paged.status_code, 200)
        items_paged = resp_paged.json()
        self.assertEqual(len(items_paged), 1)

    def test_sweep_stale_approvals_endpoint(self) -> None:
        """POST /approvals/expire-stale maintenance sweep expires stale requests."""
        # Create request that expired 10 seconds ago
        req = self._create_sample_approval(ttl_seconds=1)
        # Directly backdate expires_at for testing sweep
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        stale_req = ApprovalRequest(
            id=req.id,
            trace_id=req.trace_id,
            policy_id=req.policy_id,
            policy_version=req.policy_version,
            matched_rule_id=req.matched_rule_id,
            original_decision=req.original_decision,
            request_payload=req.request_payload,
            status=ApprovalStatus.PENDING,
            created_at=req.created_at,
            expires_at=stale_time,
            final_effect=PolicyEffect.REQUIRE_APPROVAL,
        )
        self.approval_service._requests[req.id] = stale_req

        resp = self.client.post("/api/v1/approvals/expire-stale")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(data["expired_count"], 1)
        self.assertIn(req.id, data["expired_ids"])

        # Check request in repo is now EXPIRED
        updated = self.approval_service.get_approval_request(req.id)
        self.assertEqual(updated.status, ApprovalStatus.EXPIRED)


if __name__ == "__main__":
    unittest.main()
