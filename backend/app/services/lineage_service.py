"""Core data lineage and provenance service for AMGR.

Provides deterministic, bounded graph traversal and provenance queries across
persisted FlowEventRecord, LineageRecord, GraphNode, and GraphEdge models.

Cross-Application Event Scoping Semantics:
    When a runtime flow event connects components residing in different application
    boundaries (for example, an origin node in application 'checkout-service' invoking
    a destination node in application 'payment-gateway'), AMGR treats the event as a
    cross-application interaction.
    Under the active scoping rules:
    1. If scoped by application_id='checkout-service', the event is visible because
       its origin component belongs to 'checkout-service'.
    2. If scoped by application_id='payment-gateway', the event is visible because
       its destination component belongs to 'payment-gateway'.
    3. The service does NOT claim that the entire event or both participating components
       belong exclusively to the scoped application.
    4. Associated GraphNode and GraphEdge records returned alongside events are strictly
       constrained to those satisfying the active environment and application scope:
       - Associated GraphNode records are filtered to only return nodes matching the active
         environment and application_id. Nodes outside the active application boundary are
         omitted from the response nodes list and hop node references.
       - Associated GraphEdge records (which do not possess native application_id or
         environment columns in the database schema) are constrained via their recorded
         source and destination endpoints. An edge is included only when both endpoints
         satisfy the active environment scope (if specified) and at least one endpoint
         belongs to the active application (if specified). Edges where neither endpoint
         belongs to the active application are omitted.
       - Associated LineageRecord entries are constrained to those originating from events
         within the active scope.

Completeness and Traversal Semantics:
    - Target event is always included in the traversal result at depth 0 and counts toward limit.
    - With limit=1, returns only the target event. If eligible descendants or ancestors remain,
      is_truncated=True is reported with truncation_reason='result_limit'.
    - Downstream traversal uses bounded queries to detect additional eligible descendants without
      loading unbounded child sets into memory.
    - Completeness counters:
      * returned_count equals len(hops) (the number of event records actually included in the payload).
      * total_count represents the number of distinct eligible events discovered within the bounded
        traversal, not the total count of all matching records or descendants in the database.

Schema Limitation Documentation:
    GraphEdge does not possess direct application_id or environment columns. Therefore,
    scope is enforced indirectly via inner joins against GraphNode on source_node_id and
    destination_node_id. Both endpoints must match the environment filter, and at least
    one endpoint must match the application_id filter. We do not invent an artificial
    application association for the edge itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

try:
    from backend.app.models.flow_event import FlowEventRecord
    from backend.app.models.graph_edge import GraphEdge
    from backend.app.models.graph_node import GraphNode
    from backend.app.models.lineage_record import LineageRecord
    from backend.app.schemas.lineage import (
        FlowLineageResponse,
        LineageRecord as LineageRecordSchema,
    )
except ImportError:
    from app.models.flow_event import FlowEventRecord
    from app.models.graph_edge import GraphEdge
    from app.models.graph_node import GraphNode
    from app.models.lineage_record import LineageRecord
    from app.schemas.lineage import (
        FlowLineageResponse,
        LineageRecord as LineageRecordSchema,
    )

UNRESOLVED_PARENT_KEY: str = "_unresolved_parent_id"

# Traversal & Pagination Guardrails
DEFAULT_MAX_DEPTH: int = 10
MAX_DEPTH_LIMIT: int = 100

DEFAULT_LIMIT: int = 100
MAX_LIMIT: int = 1000


def _clamp_depth(max_depth: int | None) -> int:
    """Clamp traversal depth within documented boundaries [1, MAX_DEPTH_LIMIT].

    If max_depth is None, returns DEFAULT_MAX_DEPTH (10).
    If max_depth is less than 1, clamps to 1.
    If max_depth exceeds MAX_DEPTH_LIMIT (100), clamps to 100.
    """
    if max_depth is None:
        return DEFAULT_MAX_DEPTH
    if max_depth < 1:
        return 1
    return min(max_depth, MAX_DEPTH_LIMIT)


def _clamp_limit(limit: int | None) -> int:
    """Clamp result count within documented boundaries [1, MAX_LIMIT].

    If limit is None, returns DEFAULT_LIMIT (100).
    If limit is less than 1, clamps to 1.
    If limit exceeds MAX_LIMIT (1000), clamps to 1000.
    """
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1:
        return 1
    return min(limit, MAX_LIMIT)


def _coerce_uuid(val: UUID | str | None) -> UUID | None:
    """Safely coerce a string or UUID value to UUID, returning None if invalid."""
    if val is None:
        return None
    if isinstance(val, UUID):
        return val
    try:
        return UUID(str(val).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _apply_event_scope(
    stmt: Any,
    *,
    flow_id: UUID | None = None,
    environment: str | None = None,
    application_id: str | None = None,
) -> Any:
    """Apply flow, environment, and application scope constraints to a FlowEventRecord query.

    When application_id is provided, joins source and destination GraphNode records
    and constrains the query to events where either the origin or target component
    belongs to that application boundary. Events with unresolvable node references
    or components outside the application boundary are excluded from results.
    """
    if flow_id is not None:
        stmt = stmt.where(FlowEventRecord.flow_id == flow_id)
    if environment is not None:
        stmt = stmt.where(FlowEventRecord.environment == environment)
    if application_id is not None:
        src_node = aliased(GraphNode, name="scope_src_node")
        dst_node = aliased(GraphNode, name="scope_dst_node")
        stmt = (
            stmt.outerjoin(src_node, FlowEventRecord.source_node_id == src_node.node_id)
            .outerjoin(dst_node, FlowEventRecord.destination_node_id == dst_node.node_id)
            .where(
                or_(
                    src_node.application_id == application_id,
                    dst_node.application_id == application_id,
                )
            )
        )
    return stmt


def _fetch_associated_records(
    session: Session,
    events: list[FlowEventRecord],
    *,
    environment: str | None = None,
    application_id: str | None = None,
) -> tuple[dict[UUID, LineageRecord], dict[UUID, GraphNode], dict[UUID, GraphEdge]]:
    """Batch-fetch associated LineageRecord, GraphNode, and GraphEdge instances with scope enforcement.

    Enforces environment and application restrictions across associated entities:
    - LineageRecord: Linked via originating_event_id to events that have already satisfied
      the active scope filters.
    - GraphNode: Filtered strictly by environment and application_id where provided. Nodes
      belonging to other applications or environments are omitted.
    - GraphEdge: Because GraphEdge lacks application_id and environment columns, its recorded
      endpoints (source_node_id, destination_node_id) are joined against GraphNode to enforce
      scope. Both endpoints must belong to the active environment (if specified), and at least
      one endpoint must belong to application_id (if specified). Edges failing these endpoint
      constraints are excluded.
    """
    if not events:
        return {}, {}, {}

    event_ids = [e.event_id for e in events]

    # 1. Lineage Records
    l_query = select(LineageRecord).where(LineageRecord.originating_event_id.in_(event_ids))
    lineage_records = list(session.execute(l_query).scalars().all())
    lineage_map = {r.originating_event_id: r for r in lineage_records}

    # 2. Graph Nodes
    node_ids: set[UUID] = set()
    for e in events:
        if e.source_node_id:
            node_ids.add(e.source_node_id)
        if e.destination_node_id:
            node_ids.add(e.destination_node_id)

    nodes_map: dict[UUID, GraphNode] = {}
    if node_ids:
        node_query = select(GraphNode).where(GraphNode.node_id.in_(node_ids))
        if environment is not None:
            node_query = node_query.where(GraphNode.environment == environment)
        if application_id is not None:
            node_query = node_query.where(GraphNode.application_id == application_id)
        nodes_list = list(session.execute(node_query).scalars().all())
        nodes_map = {n.node_id: n for n in nodes_list}

    # 3. Graph Edges
    edge_ids = {e.edge_id for e in events if e.edge_id is not None}
    edges_map: dict[UUID, GraphEdge] = {}
    if edge_ids:
        edge_query = select(GraphEdge).where(GraphEdge.edge_id.in_(edge_ids))
        if environment is not None or application_id is not None:
            src_node = aliased(GraphNode, name="edge_scope_src")
            dst_node = aliased(GraphNode, name="edge_scope_dst")
            edge_query = (
                edge_query.join(src_node, GraphEdge.source_node_id == src_node.node_id)
                .join(dst_node, GraphEdge.destination_node_id == dst_node.node_id)
            )
            if environment is not None:
                edge_query = edge_query.where(
                    src_node.environment == environment,
                    dst_node.environment == environment,
                )
            if application_id is not None:
                edge_query = edge_query.where(
                    or_(
                        src_node.application_id == application_id,
                        dst_node.application_id == application_id,
                    )
                )
        edges_list = list(session.execute(edge_query).scalars().all())
        edges_map = {edge.edge_id: edge for edge in edges_list}

    return lineage_map, nodes_map, edges_map


@dataclass
class TruncationMetadata:
    """Metadata describing traversal completeness, bounding, and termination reasons.

    Attributes:
        is_truncated: True if the returned results are incomplete due to depth limits,
            result limits, cycle termination, or missing/out-of-scope parent events.
        truncation_reason: Specific reason why traversal did not retrieve the entire
            subgraph/chain. Possible values: 'depth_limit', 'result_limit', 'cycle_detected',
            'missing_parent', or None if traversal completed fully without truncation.
        depth_reached: The maximum traversal depth reached from the target event (0 = target only).
        total_count: Total count of distinct eligible events discovered during bounded traversal.
            Includes traversed events and any discovered boundary event that triggered truncation.
            For flow-level and component queries, represents total matching records in the database.
        returned_count: The number of event hops/records actually returned in this response payload.
        reached_root: For upstream traversal, True only if an authentic root event was reached
            (parent_event_id is None and no unresolved parent marker). False if traversal terminated
            at a missing/unresolved parent, cycle, or limit. None for downstream or flow queries.
        has_cycle: True if a cycle was encountered in the causal chain/graph.
        missing_parent_id: If upstream traversal encountered an unresolved, missing, or
            out-of-scope parent, stores the UUID of that parent event; otherwise None.
    """

    is_truncated: bool = False
    truncation_reason: str | None = None  # "depth_limit", "result_limit", "cycle_detected", "missing_parent", or None
    depth_reached: int = 0
    total_count: int = 0
    returned_count: int = 0
    reached_root: bool | None = None
    has_cycle: bool = False
    missing_parent_id: UUID | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize truncation metadata to a JSON-compatible dictionary."""
        return {
            "is_truncated": self.is_truncated,
            "truncation_reason": self.truncation_reason,
            "depth_reached": self.depth_reached,
            "total_count": self.total_count,
            "returned_count": self.returned_count,
            "reached_root": self.reached_root,
            "has_cycle": self.has_cycle,
            "missing_parent_id": str(self.missing_parent_id) if self.missing_parent_id else None,
        }


@dataclass
class FlowLineageResult:
    """Service-level result container for flow-level lineage queries."""

    flow_id: UUID
    records: list[LineageRecord]
    total_records: int
    is_truncated: bool
    limit: int
    offset: int
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Serialize flow lineage result to a JSON-compatible dictionary."""
        return {
            "flow_id": str(self.flow_id),
            "records": [
                {
                    "record_id": str(r.record_id),
                    "flow_id": str(r.flow_id),
                    "originating_event_id": str(r.originating_event_id),
                    "parent_event_id": str(r.parent_event_id) if r.parent_event_id else None,
                    "source_node_id": str(r.source_node_id) if r.source_node_id else None,
                    "destination_node_id": str(r.destination_node_id) if r.destination_node_id else None,
                    "lineage_classification": r.lineage_classification,
                    "evidence_source": r.evidence_source,
                    "action": r.action,
                    "data_labels": list(r.data_labels or []),
                    "transformation_metadata": dict(r.transformation_metadata or {}),
                    "depth": r.depth,
                    "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
                }
                for r in self.records
            ],
            "total_records": self.total_records,
            "is_truncated": self.is_truncated,
            "limit": self.limit,
            "offset": self.offset,
            "generated_at": self.generated_at.isoformat(),
        }

    def to_flow_lineage_response(self) -> FlowLineageResponse:
        """Convert to the existing FlowLineageResponse Pydantic schema."""
        return FlowLineageResponse(
            flow_id=self.flow_id,
            records=[LineageRecordSchema.model_validate(r) for r in self.records],
            total_records=self.total_records,
            generated_at=self.generated_at,
        )


@dataclass
class LineageHop:
    """Represents a single step in an upstream or downstream lineage path."""

    depth: int
    event: FlowEventRecord
    lineage_record: LineageRecord | None = None
    source_node: GraphNode | None = None
    destination_node: GraphNode | None = None
    edge: GraphEdge | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize hop to a JSON-compatible dictionary."""
        return {
            "depth": self.depth,
            "event": self.event.to_flow_event_dict(),
            "lineage_record": {
                "record_id": str(self.lineage_record.record_id),
                "flow_id": str(self.lineage_record.flow_id),
                "originating_event_id": str(self.lineage_record.originating_event_id),
                "parent_event_id": str(self.lineage_record.parent_event_id)
                if self.lineage_record.parent_event_id
                else None,
                "lineage_classification": self.lineage_record.lineage_classification,
                "evidence_source": self.lineage_record.evidence_source,
                "action": self.lineage_record.action,
                "data_labels": list(self.lineage_record.data_labels or []),
                "transformation_metadata": dict(self.lineage_record.transformation_metadata or {}),
                "depth": self.lineage_record.depth,
                "recorded_at": self.lineage_record.recorded_at.isoformat()
                if self.lineage_record.recorded_at
                else None,
            }
            if self.lineage_record
            else None,
            "source_node": {
                "node_id": str(self.source_node.node_id),
                "component_type": self.source_node.component_type,
                "external_component_id": self.source_node.external_component_id,
                "display_name": self.source_node.display_name,
                "environment": self.source_node.environment,
                "application_id": self.source_node.application_id,
            }
            if self.source_node
            else None,
            "destination_node": {
                "node_id": str(self.destination_node.node_id),
                "component_type": self.destination_node.component_type,
                "external_component_id": self.destination_node.external_component_id,
                "display_name": self.destination_node.display_name,
                "environment": self.destination_node.environment,
                "application_id": self.destination_node.application_id,
            }
            if self.destination_node
            else None,
            "edge": {
                "edge_id": str(self.edge.edge_id),
                "source_node_id": str(self.edge.source_node_id),
                "destination_node_id": str(self.edge.destination_node_id),
                "action": self.edge.action,
                "total_events_count": self.edge.total_events_count,
                "last_status": self.edge.last_status,
                "data_labels": list(self.edge.data_labels or []),
            }
            if self.edge
            else None,
        }


@dataclass
class LineageTraversalResult:
    """Service-level result container for upstream and downstream traversal queries."""

    target_event_id: UUID
    direction: str  # "upstream" or "downstream"
    hops: list[LineageHop]
    events: list[FlowEventRecord]
    lineage_records: list[LineageRecord]
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    metadata: TruncationMetadata
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Serialize traversal result to a JSON-compatible dictionary."""
        return {
            "target_event_id": str(self.target_event_id),
            "direction": self.direction,
            "hops": [h.to_dict() for h in self.hops],
            "events": [e.to_flow_event_dict() for e in self.events],
            "lineage_records": [
                {
                    "record_id": str(r.record_id),
                    "flow_id": str(r.flow_id),
                    "originating_event_id": str(r.originating_event_id),
                    "parent_event_id": str(r.parent_event_id) if r.parent_event_id else None,
                    "lineage_classification": r.lineage_classification,
                    "evidence_source": r.evidence_source,
                    "action": r.action,
                    "data_labels": list(r.data_labels or []),
                    "transformation_metadata": dict(r.transformation_metadata or {}),
                    "depth": r.depth,
                    "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
                }
                for r in self.lineage_records
            ],
            "nodes": [
                {
                    "node_id": str(n.node_id),
                    "component_type": n.component_type,
                    "external_component_id": n.external_component_id,
                    "display_name": n.display_name,
                    "environment": n.environment,
                    "application_id": n.application_id,
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "edge_id": str(e.edge_id),
                    "source_node_id": str(e.source_node_id),
                    "destination_node_id": str(e.destination_node_id),
                    "action": e.action,
                    "total_events_count": e.total_events_count,
                    "last_status": e.last_status,
                    "data_labels": list(e.data_labels or []),
                }
                for e in self.edges
            ],
            "metadata": self.metadata.to_dict(),
            "generated_at": self.generated_at.isoformat(),
        }


@dataclass
class ComponentEventsResult:
    """Service-level result container for component event and interaction queries."""

    component_identifier: str
    events: list[FlowEventRecord]
    lineage_records: list[LineageRecord]
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    total_count: int
    limit: int
    offset: int
    is_truncated: bool
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Serialize component events result to a JSON-compatible dictionary."""
        return {
            "component_identifier": self.component_identifier,
            "events": [e.to_flow_event_dict() for e in self.events],
            "lineage_records": [
                {
                    "record_id": str(r.record_id),
                    "flow_id": str(r.flow_id),
                    "originating_event_id": str(r.originating_event_id),
                    "parent_event_id": str(r.parent_event_id) if r.parent_event_id else None,
                    "lineage_classification": r.lineage_classification,
                    "evidence_source": r.evidence_source,
                    "action": r.action,
                    "data_labels": list(r.data_labels or []),
                    "transformation_metadata": dict(r.transformation_metadata or {}),
                    "depth": r.depth,
                    "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
                }
                for r in self.lineage_records
            ],
            "nodes": [
                {
                    "node_id": str(n.node_id),
                    "component_type": n.component_type,
                    "external_component_id": n.external_component_id,
                    "display_name": n.display_name,
                    "environment": n.environment,
                    "application_id": n.application_id,
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "edge_id": str(e.edge_id),
                    "source_node_id": str(e.source_node_id),
                    "destination_node_id": str(e.destination_node_id),
                    "action": e.action,
                    "total_events_count": e.total_events_count,
                    "last_status": e.last_status,
                    "data_labels": list(e.data_labels or []),
                }
                for e in self.edges
            ],
            "total_count": self.total_count,
            "limit": self.limit,
            "offset": self.offset,
            "is_truncated": self.is_truncated,
            "generated_at": self.generated_at.isoformat(),
        }


class LineageService:
    """Core lineage and provenance service for AMGR runtime flows.

    Provides deterministic, bounded graph traversal and provenance queries across
    persisted FlowEventRecord, LineageRecord, GraphNode, and GraphEdge models.
    """

    def __init__(self) -> None:
        self._implemented = True

    def get_flow_lineage(
        self,
        session: Session,
        flow_id: UUID | str,
        *,
        environment: str | None = None,
        application_id: str | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> FlowLineageResult:
        """Retrieve lineage records for a specific flow session.

        Applies database-level filters for environment and application scope when provided,
        and enforces deterministic chronological ordering.
        """
        parsed_flow_id = _coerce_uuid(flow_id)
        if parsed_flow_id is None:
            raise ValueError(f"Invalid flow_id: {flow_id!r}")

        clamped_limit = _clamp_limit(limit)
        clamped_offset = max(0, offset)

        query = select(LineageRecord).where(LineageRecord.flow_id == parsed_flow_id)
        count_query = (
            select(func.count(LineageRecord.record_id))
            .select_from(LineageRecord)
            .where(LineageRecord.flow_id == parsed_flow_id)
        )

        if environment is not None:
            ev = aliased(FlowEventRecord, name="flow_ev_env")
            query = query.join(
                ev,
                LineageRecord.originating_event_id == ev.event_id,
            ).where(ev.environment == environment)
            count_query = count_query.join(
                ev,
                LineageRecord.originating_event_id == ev.event_id,
            ).where(ev.environment == environment)

        if application_id is not None:
            src_node = aliased(GraphNode, name="flow_src_node")
            dst_node = aliased(GraphNode, name="flow_dst_node")
            app_filter = or_(
                src_node.application_id == application_id,
                dst_node.application_id == application_id,
            )
            query = (
                query.outerjoin(src_node, LineageRecord.source_node_id == src_node.node_id)
                .outerjoin(dst_node, LineageRecord.destination_node_id == dst_node.node_id)
                .where(app_filter)
            )
            count_query = (
                count_query.outerjoin(src_node, LineageRecord.source_node_id == src_node.node_id)
                .outerjoin(dst_node, LineageRecord.destination_node_id == dst_node.node_id)
                .where(app_filter)
            )

        total_records = session.execute(count_query).scalar_one()

        query = (
            query.order_by(LineageRecord.recorded_at.asc(), LineageRecord.record_id.asc())
            .offset(clamped_offset)
            .limit(clamped_limit)
        )
        records = list(session.execute(query).scalars().all())
        is_truncated = (clamped_offset + len(records)) < total_records

        return FlowLineageResult(
            flow_id=parsed_flow_id,
            records=records,
            total_records=total_records,
            is_truncated=is_truncated,
            limit=clamped_limit,
            offset=clamped_offset,
        )

    def traverse_upstream(
        self,
        session: Session,
        event_id: UUID | str,
        *,
        flow_id: UUID | str | None = None,
        environment: str | None = None,
        application_id: str | None = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        limit: int = DEFAULT_LIMIT,
    ) -> LineageTraversalResult:
        """Traverse upstream from a known event through explicit parent-event relationships.

        Follows explicit parent_event_id linkages up the causal DAG, avoiding cycle traps,
        respecting depth and result limits, enforcing application and environment scopes on
        the target and all ancestors, and returning truncation metadata indicating whether
        the authentic root was reached or a parent is missing/unresolved/out-of-scope.

        Intended Semantics:
            - The target event is included in the result at depth 0 and counts toward the limit.
            - With limit=1, returns only the target event. If eligible ancestors remain,
              is_truncated=True is reported with truncation_reason='result_limit'.
            - Never infers causality from timestamps or graph edges; only explicit parent_event_id
              DAG linkages are traversed.
            - total_count represents the number of distinct eligible events discovered within the bounded
              traversal, not the total number of all database ancestors.
            - returned_count equals the number of event records actually included in the returned payload (len(hops)).
        """
        parsed_event_id = _coerce_uuid(event_id)
        if parsed_event_id is None:
            raise ValueError(f"Invalid event_id: {event_id!r}")

        parsed_flow_id = _coerce_uuid(flow_id) if flow_id is not None else None
        if flow_id is not None and parsed_flow_id is None:
            raise ValueError(f"Invalid flow_id: {flow_id!r}")

        clamped_max_depth = _clamp_depth(max_depth)
        clamped_limit = _clamp_limit(limit)

        empty_metadata = TruncationMetadata(
            is_truncated=False,
            truncation_reason=None,
            depth_reached=0,
            total_count=0,
            returned_count=0,
            reached_root=False,
            has_cycle=False,
            missing_parent_id=None,
        )

        # Look up target event with flow, environment, and application scope constraints
        target_stmt = select(FlowEventRecord).where(FlowEventRecord.event_id == parsed_event_id)
        target_stmt = _apply_event_scope(
            target_stmt,
            flow_id=parsed_flow_id,
            environment=environment,
            application_id=application_id,
        )
        target_event = session.execute(target_stmt).scalar_one_or_none()

        if target_event is None:
            return LineageTraversalResult(
                target_event_id=parsed_event_id,
                direction="upstream",
                hops=[],
                events=[],
                lineage_records=[],
                nodes=[],
                edges=[],
                metadata=empty_metadata,
            )

        visited_ids: set[UUID] = set()
        chain: list[tuple[FlowEventRecord, int]] = []
        curr_event: FlowEventRecord | None = target_event
        curr_depth = 0
        has_cycle = False
        reached_root = False
        missing_parent_id: UUID | None = None
        is_truncated = False
        truncation_reason: str | None = None

        while curr_event is not None:
            if curr_event.event_id in visited_ids:
                has_cycle = True
                is_truncated = True
                if truncation_reason is None:
                    truncation_reason = "cycle_detected"
                reached_root = False
                break
            visited_ids.add(curr_event.event_id)
            chain.append((curr_event, curr_depth))

            parent_id = curr_event.parent_event_id
            meta = curr_event.event_metadata or {}
            unresolved = meta.get(UNRESOLVED_PARENT_KEY)

            # Check if this node is an authentic root (no parent and no unresolved marker)
            if parent_id is None and unresolved is None:
                reached_root = True
                break

            # Result limit check: target event counts toward limit
            if len(chain) >= clamped_limit:
                if parent_id is not None:
                    parent_check = select(FlowEventRecord.event_id).where(
                        FlowEventRecord.event_id == parent_id
                    )
                    parent_check = _apply_event_scope(
                        parent_check,
                        flow_id=parsed_flow_id,
                        environment=environment,
                        application_id=application_id,
                    )
                    eligible_parent_id = session.execute(parent_check).scalar_one_or_none()
                    if eligible_parent_id is not None:
                        is_truncated = True
                        truncation_reason = "result_limit"
                        reached_root = False
                    else:
                        is_truncated = True
                        truncation_reason = "missing_parent"
                        missing_parent_id = parent_id
                        reached_root = False
                else:
                    is_truncated = True
                    truncation_reason = "missing_parent"
                    missing_parent_id = _coerce_uuid(unresolved)
                    reached_root = False
                break

            # Depth limit check: count only hops satisfying active scope
            if curr_depth >= clamped_max_depth:
                if parent_id is not None:
                    parent_check = select(FlowEventRecord.event_id).where(
                        FlowEventRecord.event_id == parent_id
                    )
                    parent_check = _apply_event_scope(
                        parent_check,
                        flow_id=parsed_flow_id,
                        environment=environment,
                        application_id=application_id,
                    )
                    eligible_parent_id = session.execute(parent_check).scalar_one_or_none()
                    if eligible_parent_id is not None:
                        is_truncated = True
                        truncation_reason = "depth_limit"
                        reached_root = False
                    else:
                        is_truncated = True
                        truncation_reason = "missing_parent"
                        missing_parent_id = parent_id
                        reached_root = False
                else:
                    is_truncated = True
                    truncation_reason = "missing_parent"
                    missing_parent_id = _coerce_uuid(unresolved)
                    reached_root = False
                break

            if parent_id is None:
                missing_parent_id = _coerce_uuid(unresolved)
                reached_root = False
                is_truncated = True
                truncation_reason = "missing_parent"
                break

            # Look up parent event with identical flow, environment, and application constraints
            parent_stmt = select(FlowEventRecord).where(FlowEventRecord.event_id == parent_id)
            parent_stmt = _apply_event_scope(
                parent_stmt,
                flow_id=parsed_flow_id,
                environment=environment,
                application_id=application_id,
            )
            parent_event = session.execute(parent_stmt).scalar_one_or_none()
            if parent_event is None:
                # Parent record is missing from database or excluded by active scope
                missing_parent_id = parent_id
                reached_root = False
                is_truncated = True
                truncation_reason = "missing_parent"
                break

            curr_event = parent_event
            curr_depth += 1

        events = [e for e, _ in chain]
        lineage_map, nodes_map, edges_map = _fetch_associated_records(
            session,
            events,
            environment=environment,
            application_id=application_id,
        )

        hops: list[LineageHop] = []
        for e, d in chain:
            hops.append(
                LineageHop(
                    depth=d,
                    event=e,
                    lineage_record=lineage_map.get(e.event_id),
                    source_node=nodes_map.get(e.source_node_id) if e.source_node_id else None,
                    destination_node=nodes_map.get(e.destination_node_id)
                    if e.destination_node_id
                    else None,
                    edge=edges_map.get(e.edge_id) if e.edge_id else None,
                )
            )

        # Definition of completeness counters:
        # returned_count: Exactly equal to len(hops)
        # total_count: Total distinct eligible events discovered during bounded traversal
        total_discovered = len(chain) + (
            1
            if is_truncated
            and truncation_reason in ("result_limit", "depth_limit", "missing_parent")
            else 0
        )

        return LineageTraversalResult(
            target_event_id=parsed_event_id,
            direction="upstream",
            hops=hops,
            events=events,
            lineage_records=list(lineage_map.values()),
            nodes=list(nodes_map.values()),
            edges=list(edges_map.values()),
            metadata=TruncationMetadata(
                is_truncated=is_truncated,
                truncation_reason=truncation_reason,
                depth_reached=chain[-1][1] if chain else 0,
                total_count=total_discovered,
                returned_count=len(hops),
                reached_root=reached_root,
                has_cycle=has_cycle,
                missing_parent_id=missing_parent_id,
            ),
        )

    def traverse_downstream(
        self,
        session: Session,
        event_id: UUID | str,
        *,
        flow_id: UUID | str | None = None,
        environment: str | None = None,
        application_id: str | None = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        limit: int = DEFAULT_LIMIT,
    ) -> LineageTraversalResult:
        """Traverse downstream from a known event through recorded child-event relationships.

        Conducts a bounded breadth-first search (BFS) following explicit parent_event_id links,
        enforcing depth and result limits, constraining target and all descendants to the active
        application/environment scopes, and returning the causal blast-radius DAG.

        Intended Semantics:
            - The target event is included in the result at depth 0 and counts toward the limit.
            - With limit=1, returns only the target event. If eligible descendants remain,
              is_truncated=True is reported with truncation_reason='result_limit'.
            - Uses bounded queries (.limit(remaining_slots + 1)) to avoid materializing unbounded
              child sets in memory.
            - Never infers causality from timestamps or graph adjacency; only explicit parent_event_id
              DAG linkages are traversed.
            - total_count represents the number of distinct eligible events discovered within the bounded
              traversal, not the total number of all database descendants.
            - returned_count equals the number of event records actually included in the returned payload (len(hops)).
        """
        parsed_event_id = _coerce_uuid(event_id)
        if parsed_event_id is None:
            raise ValueError(f"Invalid event_id: {event_id!r}")

        parsed_flow_id = _coerce_uuid(flow_id) if flow_id is not None else None
        if flow_id is not None and parsed_flow_id is None:
            raise ValueError(f"Invalid flow_id: {flow_id!r}")

        clamped_max_depth = _clamp_depth(max_depth)
        clamped_limit = _clamp_limit(limit)

        empty_metadata = TruncationMetadata(
            is_truncated=False,
            truncation_reason=None,
            depth_reached=0,
            total_count=0,
            returned_count=0,
            reached_root=None,
            has_cycle=False,
            missing_parent_id=None,
        )

        # Look up target event with flow, environment, and application scope constraints
        target_stmt = select(FlowEventRecord).where(FlowEventRecord.event_id == parsed_event_id)
        target_stmt = _apply_event_scope(
            target_stmt,
            flow_id=parsed_flow_id,
            environment=environment,
            application_id=application_id,
        )
        target_event = session.execute(target_stmt).scalar_one_or_none()

        if target_event is None:
            return LineageTraversalResult(
                target_event_id=parsed_event_id,
                direction="downstream",
                hops=[],
                events=[],
                lineage_records=[],
                nodes=[],
                edges=[],
                metadata=empty_metadata,
            )

        visited_ids: set[UUID] = {target_event.event_id}
        chain: list[tuple[FlowEventRecord, int]] = [(target_event, 0)]
        current_level: list[FlowEventRecord] = [target_event]

        depth = 0
        has_cycle = False
        is_truncated = False
        truncation_reason: str | None = None

        # Handle limit=1 early with a bounded existence check to avoid unnecessary traversal
        if clamped_limit == 1:
            child_check = select(FlowEventRecord.event_id).where(
                FlowEventRecord.parent_event_id == target_event.event_id
            )
            child_check = _apply_event_scope(
                child_check,
                flow_id=parsed_flow_id,
                environment=environment,
                application_id=application_id,
            ).limit(1)
            has_child = session.execute(child_check).scalar_one_or_none() is not None
            if has_child:
                is_truncated = True
                truncation_reason = "result_limit"
            current_level = []

        while current_level and depth < clamped_max_depth:
            remaining_slots = clamped_limit - len(chain)
            if remaining_slots <= 0:
                current_ids = [e.event_id for e in current_level]
                check_stmt = select(FlowEventRecord.event_id).where(
                    FlowEventRecord.parent_event_id.in_(current_ids)
                )
                check_stmt = _apply_event_scope(
                    check_stmt,
                    flow_id=parsed_flow_id,
                    environment=environment,
                    application_id=application_id,
                ).limit(1)
                if session.execute(check_stmt).scalar_one_or_none() is not None:
                    is_truncated = True
                    if truncation_reason is None:
                        truncation_reason = "result_limit"
                break

            current_ids = [e.event_id for e in current_level]

            # Bounded child query: fetch at most remaining_slots + 1 to detect truncation safely
            child_stmt = select(FlowEventRecord).where(
                FlowEventRecord.parent_event_id.in_(current_ids)
            )
            child_stmt = _apply_event_scope(
                child_stmt,
                flow_id=parsed_flow_id,
                environment=environment,
                application_id=application_id,
            )
            child_stmt = (
                child_stmt.order_by(
                    FlowEventRecord.timestamp.asc(), FlowEventRecord.event_id.asc()
                )
                .limit(remaining_slots + 1)
            )
            children = list(session.execute(child_stmt).scalars().all())
            if not children:
                break

            if len(children) > remaining_slots:
                children_to_add = children[:remaining_slots]
                has_more = True
            else:
                children_to_add = children
                has_more = False

            next_level: list[FlowEventRecord] = []
            for ch in children_to_add:
                if ch.event_id in visited_ids:
                    has_cycle = True
                    is_truncated = True
                    if truncation_reason is None:
                        truncation_reason = "cycle_detected"
                    continue
                visited_ids.add(ch.event_id)
                chain.append((ch, depth + 1))
                next_level.append(ch)

            if has_more:
                is_truncated = True
                if truncation_reason is None:
                    truncation_reason = "result_limit"
                break

            if len(chain) >= clamped_limit:
                if not is_truncated and next_level:
                    next_ids = [e.event_id for e in next_level]
                    further_check = select(FlowEventRecord.event_id).where(
                        FlowEventRecord.parent_event_id.in_(next_ids)
                    )
                    further_check = _apply_event_scope(
                        further_check,
                        flow_id=parsed_flow_id,
                        environment=environment,
                        application_id=application_id,
                    ).limit(1)
                    if session.execute(further_check).scalar_one_or_none() is not None:
                        is_truncated = True
                        if truncation_reason is None:
                            truncation_reason = "result_limit"
                break

            current_level = next_level
            depth += 1

        # Check if depth limit truncated further eligible descendants after traversal loop exits
        if not is_truncated and chain:
            max_depth_reached = chain[-1][1]
            if max_depth_reached >= clamped_max_depth:
                last_level_ids = [e.event_id for e, d in chain if d == max_depth_reached]
                if last_level_ids:
                    depth_check = select(FlowEventRecord.event_id).where(
                        FlowEventRecord.parent_event_id.in_(last_level_ids)
                    )
                    depth_check = _apply_event_scope(
                        depth_check,
                        flow_id=parsed_flow_id,
                        environment=environment,
                        application_id=application_id,
                    ).limit(1)
                    if session.execute(depth_check).scalar_one_or_none() is not None:
                        is_truncated = True
                        truncation_reason = "depth_limit"

        events = [e for e, _ in chain]
        lineage_map, nodes_map, edges_map = _fetch_associated_records(
            session,
            events,
            environment=environment,
            application_id=application_id,
        )

        hops: list[LineageHop] = []
        for e, d in chain:
            hops.append(
                LineageHop(
                    depth=d,
                    event=e,
                    lineage_record=lineage_map.get(e.event_id),
                    source_node=nodes_map.get(e.source_node_id) if e.source_node_id else None,
                    destination_node=nodes_map.get(e.destination_node_id)
                    if e.destination_node_id
                    else None,
                    edge=edges_map.get(e.edge_id) if e.edge_id else None,
                )
            )

        total_discovered = max(
            len(visited_ids),
            len(chain) + (1 if is_truncated and truncation_reason in ("result_limit", "depth_limit") else 0),
        )

        return LineageTraversalResult(
            target_event_id=parsed_event_id,
            direction="downstream",
            hops=hops,
            events=events,
            lineage_records=list(lineage_map.values()),
            nodes=list(nodes_map.values()),
            edges=list(edges_map.values()),
            metadata=TruncationMetadata(
                is_truncated=is_truncated,
                truncation_reason=truncation_reason,
                depth_reached=chain[-1][1] if chain else 0,
                total_count=total_discovered,
                returned_count=len(hops),
                reached_root=None,
                has_cycle=has_cycle,
                missing_parent_id=None,
            ),
        )

    def get_component_events(
        self,
        session: Session,
        component_identifier: str | UUID,
        *,
        environment: str | None = None,
        application_id: str | None = None,
        flow_id: UUID | str | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> ComponentEventsResult:
        """Retrieve events involving a specific graph component.

        Matches by external_component_id or node_id strictly through a scoped GraphNode
        subquery without direct UUID bypasses. Applies environment and application filters
        identically across count and event retrieval queries, paginating before resolving
        related entities and constraining associated records to the active scope.
        """
        clamped_limit = _clamp_limit(limit)
        clamped_offset = max(0, offset)
        parsed_flow_id = _coerce_uuid(flow_id) if flow_id is not None else None
        if flow_id is not None and parsed_flow_id is None:
            raise ValueError(f"Invalid flow_id: {flow_id!r}")

        str_identifier = str(component_identifier).strip() if component_identifier is not None else ""
        if not str_identifier:
            raise ValueError("component_identifier must not be empty.")
        target_uuid = _coerce_uuid(component_identifier)

        # Match component nodes strictly through scoped GraphNode lookup
        # Both UUID and string component identifiers MUST satisfy the environment and application filters
        node_id_subquery = select(GraphNode.node_id)
        if target_uuid is not None:
            node_id_subquery = node_id_subquery.where(
                or_(
                    GraphNode.node_id == target_uuid,
                    GraphNode.external_component_id == str_identifier,
                )
            )
        else:
            node_id_subquery = node_id_subquery.where(
                GraphNode.external_component_id == str_identifier
            )

        if environment is not None:
            node_id_subquery = node_id_subquery.where(GraphNode.environment == environment)
        if application_id is not None:
            node_id_subquery = node_id_subquery.where(GraphNode.application_id == application_id)

        # Scope component filter: only events connected to in-scope component nodes
        # Direct UUID matching on FlowEventRecord without node_id_subquery is forbidden
        event_component_filter = or_(
            FlowEventRecord.source_node_id.in_(node_id_subquery),
            FlowEventRecord.destination_node_id.in_(node_id_subquery),
        )

        event_query = select(FlowEventRecord).where(event_component_filter)
        count_query = (
            select(func.count(FlowEventRecord.event_id))
            .select_from(FlowEventRecord)
            .where(event_component_filter)
        )

        event_query = _apply_event_scope(
            event_query,
            flow_id=parsed_flow_id,
            environment=environment,
            application_id=application_id,
        )
        count_query = _apply_event_scope(
            count_query,
            flow_id=parsed_flow_id,
            environment=environment,
            application_id=application_id,
        )

        total_count = session.execute(count_query).scalar_one()

        event_query = (
            event_query.order_by(
                FlowEventRecord.timestamp.desc(), FlowEventRecord.event_id.asc()
            )
            .offset(clamped_offset)
            .limit(clamped_limit)
        )
        events = list(session.execute(event_query).scalars().all())
        is_truncated = (clamped_offset + len(events)) < total_count

        lineage_map, nodes_map, edges_map = _fetch_associated_records(
            session,
            events,
            environment=environment,
            application_id=application_id,
        )

        return ComponentEventsResult(
            component_identifier=str_identifier,
            events=events,
            lineage_records=list(lineage_map.values()),
            nodes=list(nodes_map.values()),
            edges=list(edges_map.values()),
            total_count=total_count,
            limit=clamped_limit,
            offset=clamped_offset,
            is_truncated=is_truncated,
        )

    get_events_by_component = get_component_events

    def get_event_lineage(
        self,
        session: Session,
        event_id: UUID | str,
        *,
        environment: str | None = None,
        application_id: str | None = None,
    ) -> LineageHop | None:
        """Retrieve the direct lineage record and graph context for a single event."""
        parsed_event_id = _coerce_uuid(event_id)
        if parsed_event_id is None:
            raise ValueError(f"Invalid event_id: {event_id!r}")

        stmt = select(FlowEventRecord).where(FlowEventRecord.event_id == parsed_event_id)
        stmt = _apply_event_scope(
            stmt,
            environment=environment,
            application_id=application_id,
        )
        event = session.execute(stmt).scalar_one_or_none()
        if event is None:
            return None

        lineage_map, nodes_map, edges_map = _fetch_associated_records(
            session,
            [event],
            environment=environment,
            application_id=application_id,
        )

        lineage_record = lineage_map.get(event.event_id)
        source_node = nodes_map.get(event.source_node_id) if event.source_node_id else None
        dest_node = (
            nodes_map.get(event.destination_node_id) if event.destination_node_id else None
        )
        edge = edges_map.get(event.edge_id) if event.edge_id else None

        depth = lineage_record.depth if lineage_record and lineage_record.depth is not None else 0

        return LineageHop(
            depth=depth,
            event=event,
            lineage_record=lineage_record,
            source_node=source_node,
            destination_node=dest_node,
            edge=edge,
        )

    def resolve_lineage(
        self,
        session: Session | None = None,
        *args: Any,
        flow_id: UUID | str | None = None,
        event_id: UUID | str | None = None,
        component_id: str | UUID | None = None,
        direction: str = "upstream",
        environment: str | None = None,
        application_id: str | None = None,
        max_depth: int | None = None,
        limit: int | None = None,
        offset: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """Resolve flow lineage, event traversal, or component interactions.

        Maintains backward compatibility with the placeholder signature while delegating
        to specific bounded service queries without truthiness defaults.
        """
        resolved_session = session
        if resolved_session is None:
            for arg in args:
                if isinstance(arg, Session):
                    resolved_session = arg
                    break

        if resolved_session is None:
            raise ValueError("A valid SQLAlchemy Session is required to resolve lineage.")

        effective_limit = limit if limit is not None else DEFAULT_LIMIT
        effective_offset = offset if offset is not None else 0
        effective_max_depth = max_depth if max_depth is not None else DEFAULT_MAX_DEPTH

        if flow_id is not None:
            return self.get_flow_lineage(
                resolved_session,
                flow_id=flow_id,
                environment=environment,
                application_id=application_id,
                limit=effective_limit,
                offset=effective_offset,
            )

        if event_id is not None:
            normalized_direction = str(direction).lower().strip() if direction else "upstream"
            if normalized_direction not in ("upstream", "downstream"):
                raise ValueError(
                    f"Unsupported traversal direction: {direction!r}. Must be 'upstream' or 'downstream'."
                )

            if normalized_direction == "downstream":
                return self.traverse_downstream(
                    resolved_session,
                    event_id=event_id,
                    flow_id=flow_id,
                    environment=environment,
                    application_id=application_id,
                    max_depth=effective_max_depth,
                    limit=effective_limit,
                )
            return self.traverse_upstream(
                resolved_session,
                event_id=event_id,
                flow_id=flow_id,
                environment=environment,
                application_id=application_id,
                max_depth=effective_max_depth,
                limit=effective_limit,
            )

        if component_id is not None:
            return self.get_component_events(
                resolved_session,
                component_identifier=component_id,
                environment=environment,
                application_id=application_id,
                flow_id=flow_id,
                limit=effective_limit,
                offset=effective_offset,
            )

        raise ValueError(
            "At least one of 'flow_id', 'event_id', or 'component_id' must be provided to resolve_lineage."
        )
