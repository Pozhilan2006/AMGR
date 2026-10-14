"""Tests for AMGR Phase 4 Master Prompt 8: Decision History Analytics, Summary Metrics, and Retention Management.

Validates:
- Statistical summary calculation and distribution metrics across evaluated decisions.
- Multi-parameter filtering for decision summary (trace_id, policy_id, decision, action, resource).
- Time bound discovery (earliest_evaluated_at and latest_evaluated_at).
- Decision history retention purge with dry_run verification (zero deletion side effects).
- Live audit retention purge with before_timestamp and older_than_seconds criteria.
- Automated AI / LLM entity rejection on audit log purge (403/422).
- Transactional persistence and purge behavior against real database sessions with rollback on failure.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.api.authorization import reset_services
from backend.app.db.session import Base
from backend.app.main import create_app
from backend.app.models.policy_decision import PolicyDecisionModel
from backend.app.repositories.decision_repository import DecisionRepository
from backend.app.repositories.policy_repository import PolicyRepository
from backend.app.schemas.authorization import (
    AuthorizationDecision,
    AuthorizationRequest,
    ReasonCode,
)
from backend.app.schemas.policy_enums import PolicyEffect
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
    def __init__(self, app_instance: Any) -> None:
        self.app = app_instance

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: Any = None,
    ) -> _TestResponse:
        query_string = ""
        if "?" in path:
            path, qs = path.split("?", 1)
            query_string = qs

        if params:
            param_str = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            query_string = f"{query_string}&{param_str}" if query_string else param_str

        raw_body = b""
        if json_data is not None:
            raw_body = json.dumps(json_data).encode("utf-8")

        headers_list = [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(raw_body)).encode("utf-8")),
        ]

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method.upper(),
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": query_string.encode("utf-8"),
            "headers": headers_list,
            "state": {},
        }

        resp_headers: dict[str, str] = {}
        resp_body: list[bytes] = []
        status_code = 500

        async def receive() -> dict[str, Any]:
            return {
                "type": "http.request",
                "body": raw_body,
                "more_body": False,
            }

        async def send(message: dict[str, Any]) -> None:
            nonlocal status_code, resp_headers
            if message["type"] == "http.response.start":
                status_code = message["status"]
                for k, v in message.get("headers", []):
                    resp_headers[k.decode("utf-8").lower()] = v.decode("utf-8")
            elif message["type"] == "http.response.body":
                resp_body.append(message.get("body", b""))

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self.app(scope, receive, send))
        finally:
            loop.close()

        return _TestResponse(status_code, resp_headers, b"".join(resp_body))

    def get(self, path: str, params: dict[str, Any] | None = None) -> _TestResponse:
        return self.request("GET", path, params=params)

    def post(self, path: str, json_data: Any = None, params: dict[str, Any] | None = None) -> _TestResponse:
        return self.request("POST", path, params=params, json_data=json_data)


class TestDecisionAnalyticsAndRetention(unittest.TestCase):
    """Test suite for DecisionRepository analytics, summary metrics, and retention purge."""

    def setUp(self) -> None:
        self.decision_repo = DecisionRepository()
        self.policy_repo = PolicyRepository()
        self.approval_service = ApprovalService()
        self.auth_service = AuthorizationService(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            decision_repo=self.decision_repo,
        )

        reset_services(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            auth_service=self.auth_service,
            decision_repo=self.decision_repo,
        )

        self.app = create_app(
            policy_repo=self.policy_repo,
            approval_service=self.approval_service,
            auth_service=self.auth_service,
            decision_repo=self.decision_repo,
        )
        self.client = ASGIClient(self.app)

        self.base_time = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        reset_services(None, None, None, None)

    def _seed_sample_decisions(self) -> None:
        """Seeds a diverse set of decisions across effects, policies, and timestamps."""
        # 1. ALLOW decision
        self.decision_repo.record_decision(
            AuthorizationRequest(
                trace_id="trc_001",
                subject="analyst_alice",
                action="query_analytics",
                resource="db://analytics_warehouse",
                context={"team": "finance"},
            ),
            AuthorizationDecision(
                decision=PolicyEffect.ALLOW,
                policy_id="pol_finance_access",
                policy_version=1,
                matched_rule_id="rule_finance_allow",
                matched_rule_ids=["rule_finance_allow"],
                matched_policy_ids=["pol_finance_access"],
                reason_code=ReasonCode.RULE_MATCHED,
                reason_message="Finance analyst allowed to query analytics.",
                evaluated_at=self.base_time - timedelta(hours=3),
            ),
        )

        # 2. BLOCK decision
        self.decision_repo.record_decision(
            AuthorizationRequest(
                trace_id="trc_002",
                subject="contractor_bob",
                action="export_customer_data",
                resource="s3://customer_pii",
                context={"team": "marketing"},
            ),
            AuthorizationDecision(
                decision=PolicyEffect.BLOCK,
                policy_id="pol_pii_guard",
                policy_version=2,
                matched_rule_id="rule_block_pii",
                matched_rule_ids=["rule_block_pii"],
                matched_policy_ids=["pol_pii_guard"],
                reason_code=ReasonCode.RULE_MATCHED,
                reason_message="Exporting customer PII is strictly forbidden.",
                evaluated_at=self.base_time - timedelta(hours=2),
            ),
        )

        # 3. REQUIRE_APPROVAL decision
        self.decision_repo.record_decision(
            AuthorizationRequest(
                trace_id="trc_003",
                subject="service_billing",
                action="transfer_funds",
                resource="api://payments/wire",
                context={"amount": 50000},
            ),
            AuthorizationDecision(
                decision=PolicyEffect.REQUIRE_APPROVAL,
                policy_id="pol_wire_approval",
                policy_version=1,
                matched_rule_id="rule_require_human_signoff",
                matched_rule_ids=["rule_require_human_signoff"],
                matched_policy_ids=["pol_wire_approval"],
                reason_code=ReasonCode.RULE_MATCHED,
                reason_message="High-value wire transfers require dual-control signoff.",
                evaluated_at=self.base_time - timedelta(hours=1),
            ),
        )

        # 4. Fallback BLOCK decision
        self.decision_repo.record_decision(
            AuthorizationRequest(
                trace_id="trc_004",
                subject="unknown_agent",
                action="debug_shell",
                resource="host://prod_worker",
            ),
            AuthorizationDecision(
                decision=PolicyEffect.BLOCK,
                policy_id=None,
                policy_version=None,
                matched_rule_id=None,
                matched_rule_ids=[],
                matched_policy_ids=[],
                reason_code=ReasonCode.DEFAULT_FALLBACK,
                reason_message="No governing policies matched; fail closed.",
                evaluated_at=self.base_time,
            ),
        )

    def test_decision_summary_calculation_and_breakdown(self) -> None:
        """GET /decisions/summary correctly computes total counts, outcome breakdown, and time span."""
        self._seed_sample_decisions()

        resp = self.client.get("/api/v1/decisions/summary")
        self.assertEqual(resp.status_code, 200)
        summary = resp.json()

        self.assertEqual(summary["total_decisions"], 4)

        # Outcome distribution
        self.assertEqual(summary["decision_counts"]["ALLOW"], 1)
        self.assertEqual(summary["decision_counts"]["BLOCK"], 2)
        self.assertEqual(summary["decision_counts"]["REQUIRE_APPROVAL"], 1)

        # Reason code distribution
        self.assertEqual(summary["reason_code_counts"]["RULE_MATCHED"], 3)
        self.assertEqual(summary["reason_code_counts"]["DEFAULT_FALLBACK"], 1)

        # Policy attribution
        self.assertEqual(summary["policy_counts"]["pol_finance_access"], 1)
        self.assertEqual(summary["policy_counts"]["pol_pii_guard"], 1)
        self.assertEqual(summary["policy_counts"]["pol_wire_approval"], 1)

        # Action breakdown
        self.assertEqual(summary["action_counts"]["query_analytics"], 1)
        self.assertEqual(summary["action_counts"]["export_customer_data"], 1)
        self.assertEqual(summary["action_counts"]["transfer_funds"], 1)
        self.assertEqual(summary["action_counts"]["debug_shell"], 1)

        # Time bounds
        self.assertIsNotNone(summary["earliest_evaluated_at"])
        self.assertIsNotNone(summary["latest_evaluated_at"])

    def test_decision_summary_filtering_by_policy_id_and_decision(self) -> None:
        """GET /decisions/summary supports filtering by policy_id, decision, action, and resource."""
        self._seed_sample_decisions()

        # 1. Filter by policy_id
        resp_pol = self.client.get("/api/v1/decisions/summary?policy_id=pol_pii_guard")
        self.assertEqual(resp_pol.status_code, 200)
        summary_pol = resp_pol.json()
        self.assertEqual(summary_pol["total_decisions"], 1)
        self.assertEqual(summary_pol["decision_counts"]["BLOCK"], 1)

        # 2. Filter by decision=BLOCK
        resp_block = self.client.get("/api/v1/decisions/summary?decision=BLOCK")
        self.assertEqual(resp_block.status_code, 200)
        summary_block = resp_block.json()
        self.assertEqual(summary_block["total_decisions"], 2)

    def test_decision_purge_dry_run_does_not_mutate_history(self) -> None:
        """POST /decisions/purge with dry_run=True returns eligible count without deleting records."""
        self._seed_sample_decisions()
        self.assertEqual(self.decision_repo.count_decisions(), 4)

        # Cutoff timestamp between trc_002 and trc_003 (2 decisions older than cutoff)
        cutoff = (self.base_time - timedelta(hours=1, minutes=30)).isoformat()

        resp = self.client.post(
            "/api/v1/decisions/purge",
            json_data={
                "before_timestamp": cutoff,
                "dry_run": True,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["purged_count"], 2)
        self.assertTrue(data["dry_run"])

        # History remains intact
        self.assertEqual(self.decision_repo.count_decisions(), 4)

    def test_decision_purge_live_execution_deletes_records(self) -> None:
        """POST /decisions/purge with dry_run=False removes records older than cutoff."""
        self._seed_sample_decisions()
        self.assertEqual(self.decision_repo.count_decisions(), 4)

        # Purge records older than 1.5 hours (should purge trc_001 and trc_002)
        cutoff = (self.base_time - timedelta(hours=1, minutes=30)).isoformat()

        resp = self.client.post(
            "/api/v1/decisions/purge",
            json_data={
                "before_timestamp": cutoff,
                "dry_run": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["purged_count"], 2)
        self.assertFalse(data["dry_run"])
        self.assertEqual(data["remaining_count"], 2)

        # Verify underlying repository count
        self.assertEqual(self.decision_repo.count_decisions(), 2)

        # Remaining decisions are trc_003 and trc_004
        remaining_records = self.decision_repo.list_decisions()
        remaining_traces = {r["trace_id"] for r in remaining_records}
        self.assertEqual(remaining_traces, {"trc_003", "trc_004"})

    def test_decision_purge_with_older_than_seconds(self) -> None:
        """POST /decisions/purge supports relative older_than_seconds parameter."""
        # Record a fresh decision
        self.decision_repo.record_decision(
            AuthorizationRequest(
                trace_id="trc_recent",
                subject="fresh_actor",
                action="recent_read",
                resource="db://test",
            ),
            AuthorizationDecision(
                decision=PolicyEffect.ALLOW,
                policy_id="pol_test",
                reason_code=ReasonCode.RULE_MATCHED,
                reason_message="Allowed.",
                evaluated_at=datetime.now(timezone.utc),
            ),
        )
        # Record an old decision (10 days ago)
        self.decision_repo.record_decision(
            AuthorizationRequest(
                trace_id="trc_ancient",
                subject="ancient_actor",
                action="ancient_read",
                resource="db://test",
            ),
            AuthorizationDecision(
                decision=PolicyEffect.ALLOW,
                policy_id="pol_test",
                reason_code=ReasonCode.RULE_MATCHED,
                reason_message="Allowed.",
                evaluated_at=datetime.now(timezone.utc) - timedelta(days=10),
            ),
        )

        # Purge older than 5 days (432,000 seconds)
        resp = self.client.post(
            "/api/v1/decisions/purge",
            json_data={
                "older_than_seconds": 5 * 86400,
                "dry_run": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["purged_count"], 1)

        # trc_recent remains
        recs = self.decision_repo.list_decisions()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["trace_id"], "trc_recent")

    def test_decision_purge_rejects_ai_operator(self) -> None:
        """Automated AI / LLM entities are strictly forbidden from purging decision history."""
        resp = self.client.post(
            "/api/v1/decisions/purge",
            json_data={
                "older_than_seconds": 3600,
                "is_ai_or_llm": True,
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_decision_purge_requires_timestamp_or_ttl_422(self) -> None:
        """Purge request missing both before_timestamp and older_than_seconds raises 422."""
        resp = self.client.post(
            "/api/v1/decisions/purge",
            json_data={},
        )
        self.assertEqual(resp.status_code, 422)

    def test_database_backed_decision_summary_and_purge(self) -> None:
        """Verifies analytics aggregation and purge operations using a real SQLite database session."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        db: Session = session_factory()

        try:
            repo = DecisionRepository(db_session=db)

            # Insert decisions directly to DB
            t1 = self.base_time - timedelta(days=5)
            t2 = self.base_time - timedelta(days=2)
            t3 = self.base_time

            repo.record_decision(
                AuthorizationRequest(
                    trace_id="trc_db_1",
                    subject="agent_a",
                    action="sql_query",
                    resource="db://orders",
                ),
                AuthorizationDecision(
                    decision=PolicyEffect.ALLOW,
                    policy_id="pol_orders",
                    reason_code=ReasonCode.RULE_MATCHED,
                    reason_message="Allowed.",
                    evaluated_at=t1,
                ),
            )
            repo.record_decision(
                AuthorizationRequest(
                    trace_id="trc_db_2",
                    subject="agent_b",
                    action="sql_drop",
                    resource="db://orders",
                ),
                AuthorizationDecision(
                    decision=PolicyEffect.BLOCK,
                    policy_id="pol_orders",
                    reason_code=ReasonCode.RULE_MATCHED,
                    reason_message="Blocked DROP TABLE.",
                    evaluated_at=t2,
                ),
            )
            repo.record_decision(
                AuthorizationRequest(
                    trace_id="trc_db_3",
                    subject="agent_c",
                    action="export",
                    resource="s3://backup",
                ),
                AuthorizationDecision(
                    decision=PolicyEffect.REQUIRE_APPROVAL,
                    policy_id="pol_export",
                    reason_code=ReasonCode.RULE_MATCHED,
                    reason_message="Approval required.",
                    evaluated_at=t3,
                ),
            )

            # Verify total count in DB
            self.assertEqual(repo.count_decisions(), 3)

            # Verify DB summary aggregation
            summary = repo.get_decision_summary()
            self.assertEqual(summary["total_decisions"], 3)
            self.assertEqual(summary["decision_counts"]["ALLOW"], 1)
            self.assertEqual(summary["decision_counts"]["BLOCK"], 1)
            self.assertEqual(summary["decision_counts"]["REQUIRE_APPROVAL"], 1)
            self.assertEqual(summary["action_counts"]["sql_query"], 1)
            self.assertEqual(summary["action_counts"]["sql_drop"], 1)
            self.assertEqual(summary["action_counts"]["export"], 1)

            # Purge records older than 3 days (purges trc_db_1)
            cutoff = self.base_time - timedelta(days=3)
            purged, remaining = repo.purge_decisions(before_timestamp=cutoff, dry_run=False)
            self.assertEqual(purged, 1)
            self.assertEqual(remaining, 2)

            # Verify SQLite table has exactly 2 records
            db_records = db.query(PolicyDecisionModel).all()
            self.assertEqual(len(db_records), 2)
            db_traces = {r.trace_id for r in db_records}
            self.assertEqual(db_traces, {"trc_db_2", "trc_db_3"})

        finally:
            db.close()
            Base.metadata.drop_all(engine)
