"""Graph persistence service for AMGR flow graph entities."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import event as sa_event, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.ai_operator.schemas import OperatorScope
from app.models.flow_event import FlowEventRecord
from app.models.graph_edge import GraphEdge as GraphEdgeModel
from app.models.graph_node import GraphNode as GraphNodeModel
from app.repositories.graph_repository import GraphRepository
from app.schemas.flow_event import ComponentRef, FlowEvent
from app.schemas.graph import (
    GraphEdge as GraphEdgeSchema,
    GraphNode as GraphNodeSchema,
    GraphResponse,
)
from app.schemas.graph_queries import GraphNeighborhoodResponse, GraphPathResponse, GraphSnapshotResponse

# Deny-list substrings for metadata sanitization to protect secrets and prompts
FORBIDDEN_METADATA_KEYS = {
    "prompt",
    "raw_prompt",
    "system_prompt",
    "completion",
    "password",
    "secret",
    "token",
    "api_key",
    "auth",
    "authorization",
    "credential",
    "body",
    "payload",
    "input_text",
    "output_text",
}


# Lifecycle hooks & helpers to ensure in-memory transaction tracking does not leak across rollbacks or commits
def _get_tx_event_stack(session: Session) -> list[set[tuple[UUID, UUID]]]:
    """Retrieve or initialize the transaction savepoint event-tracking stack."""
    if "_tx_event_stack" not in session.info:
        session.info["_tx_event_stack"] = [set()]
    return session.info["_tx_event_stack"]


def _sync_flat_tx_events(session: Session) -> None:
    """Synchronize the flat set in session.info for fast membership checks and backward compatibility."""
    stack = session.info.get("_tx_event_stack")
    if stack:
        session.info["_tx_aggregated_event_edges"] = set().union(*stack)
    else:
        session.info.pop("_tx_aggregated_event_edges", None)


def _track_event_in_tx(session: Session, edge_id: UUID, event_id: UUID) -> None:
    """Register an event-to-edge aggregation in the current transaction layer."""
    stack = _get_tx_event_stack(session)
    stack[-1].add((edge_id, event_id))
    _sync_flat_tx_events(session)


def _is_event_tracked_in_tx(session: Session, edge_id: UUID, event_id: UUID) -> bool:
    """Check whether an event-to-edge aggregation is registered in any active transaction layer."""
    tx_events = session.info.get("_tx_aggregated_event_edges")
    return bool(tx_events and (edge_id, event_id) in tx_events)


@sa_event.listens_for(Session, "after_transaction_create")
def _on_tx_create(session: Session, transaction: Any) -> None:
    """Push a new tracking layer when a nested transaction (savepoint) begins."""
    if getattr(transaction, "nested", False):
        stack = _get_tx_event_stack(session)
        stack.append(set())


@sa_event.listens_for(Session, "after_rollback")
def _clear_tx_aggregated_events_on_rollback(session: Session) -> None:
    """Clear transaction-scoped in-memory event tracking on rollback.

    If rolling back a nested savepoint, only the savepoint's tracking layer is discarded,
    preserving event tracking registered by the outer transaction.
    If rolling back the root transaction, all tracking state is cleared.
    """
    stack = session.info.get("_tx_event_stack")
    if not stack:
        session.info.pop("_tx_aggregated_event_edges", None)
        return

    in_nested = False
    try:
        in_nested = session.in_nested_transaction()
    except Exception:
        in_nested = False

    if in_nested and len(stack) > 1:
        stack.pop()
        _sync_flat_tx_events(session)
    else:
        session.info.pop("_tx_event_stack", None)
        session.info.pop("_tx_aggregated_event_edges", None)


@sa_event.listens_for(Session, "after_commit")
def _clear_tx_aggregated_events_on_commit(session: Session) -> None:
    """Handle commit: merges savepoint tracking into parent layer or clears on root commit.

    If committing a nested savepoint, savepoint events merge into the parent transaction layer.
    If committing the root transaction, durable persistence is complete and session tracking is cleared.
    """
    stack = session.info.get("_tx_event_stack")
    if not stack:
        session.info.pop("_tx_aggregated_event_edges", None)
        return

    in_nested = False
    try:
        in_nested = session.in_nested_transaction()
    except Exception:
        in_nested = False

    if in_nested and len(stack) > 1:
        top = stack.pop()
        stack[-1].update(top)
        _sync_flat_tx_events(session)
    else:
        session.info.pop("_tx_event_stack", None)
        session.info.pop("_tx_aggregated_event_edges", None)


def _sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Sanitize metadata dictionary, stripping secrets, raw prompts, and full payloads."""
    if not metadata or not isinstance(metadata, dict):
        return {}

    clean: dict[str, Any] = {}
    for key, val in metadata.items():
        if not isinstance(key, str):
            continue
        key_lower = key.strip().lower()
        if any(bad in key_lower for bad in FORBIDDEN_METADATA_KEYS):
            continue
        # Restrict value types to primitives / small representations
        if isinstance(val, (str, int, float, bool)) or val is None:
            clean[key] = val
        elif isinstance(val, list) and all(isinstance(x, (str, int, float, bool)) for x in val):
            clean[key] = val
        elif isinstance(val, dict):
            # Recurse one level for nested safe dictionaries
            clean_sub = _sanitize_metadata(val)
            if clean_sub:
                clean[key] = clean_sub
    return clean


class GraphService:
    """Graph persistence and query service.

    Manages resolution, upsert, and state management of GraphNode and GraphEdge
    entities within active caller-managed SQLAlchemy sessions.
    """

    def __init__(self, repository: GraphRepository | None = None) -> None:
        # The optional repository powers the read API; persistence methods below
        # use the caller-managed SQLAlchemy session directly.
        self._repository = repository
        self._implemented = True

    def get_snapshot(
        self,
        *,
        scope: OperatorScope,
        limit: int = 100,
        offset: int = 0,
        component_type: str | None = None,
        flow_id: UUID | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        action: str | None = None,
        data_label: str | None = None,
    ) -> GraphSnapshotResponse:
        if self._repository is None:
            raise ValueError("A GraphRepository is required for graph query operations.")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500.")
        if offset < 0:
            raise ValueError("offset must be non-negative.")
        return self._repository.list_snapshot(
            scope=scope, limit=limit, offset=offset, component_type=component_type,
            flow_id=flow_id, start=start, end=end, action=action, data_label=data_label,
        )

    def get_neighborhood(
        self,
        *,
        component_id: str,
        scope: OperatorScope,
        direction: str = "both",
        limit: int = 25,
        offset: int = 0,
    ) -> GraphNeighborhoodResponse:
        if self._repository is None:
            raise ValueError("A GraphRepository is required for graph query operations.")
        if not component_id.strip():
            raise ValueError("component_id is required.")
        if direction not in {"incoming", "outgoing", "both"}:
            raise ValueError("direction must be incoming, outgoing, or both.")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        if offset < 0:
            raise ValueError("offset must be non-negative.")
        entries, _ = self._repository.neighborhood(
            component_id=component_id, scope=scope, direction=direction, limit=limit, offset=offset
        )
        return GraphNeighborhoodResponse(
            component_id=component_id, neighbors=entries,
            metadata={"direction": direction, "limit": limit, "offset": offset},
            unavailable=False, message="Observed component neighbors in the authorized scope.",
        )

    def get_paths(
        self,
        *,
        source_component_id: str,
        destination_component_id: str,
        scope: OperatorScope,
        max_depth: int = 2,
        limit: int = 10,
    ) -> GraphPathResponse:
        if self._repository is None:
            raise ValueError("A GraphRepository is required for graph query operations.")
        if not source_component_id.strip() or not destination_component_id.strip():
            raise ValueError("source_component_id and destination_component_id are required.")
        if not 1 <= max_depth <= 8:
            raise ValueError("max_depth must be between 1 and 8.")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50.")
        paths = self._repository.find_paths(
            source_component_id=source_component_id,
            destination_component_id=destination_component_id,
            scope=scope, max_depth=max_depth, limit=limit,
        )
        if not paths:
            return GraphPathResponse(
                source_component_id=source_component_id,
                destination_component_id=destination_component_id,
                paths=[], completeness="complete",
                message="No supported component path was found in the authorized scope.",
            )
        return GraphPathResponse(
            source_component_id=source_component_id,
            destination_component_id=destination_component_id,
            paths=[
                {
                    "path_id": f"path:{index}", "nodes": path,
                    "edges": [f"edge:{path[i]}->{path[i + 1]}" for i in range(len(path) - 1)],
                    "evidence": ["recorded_component_interactions"],
                }
                for index, path in enumerate(paths, start=1)
            ],
            completeness="incomplete" if len(paths) >= limit else "complete",
            message="Paths represent recorded component interactions only; they do not prove data movement.",
        )

    def resolve_node(
        self,
        session: Session,
        component: ComponentRef | dict[str, Any] | str,
        *,
        component_type: str | None = None,
        environment: str = "development",
        application_id: str | None = None,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        observed_at: datetime | None = None,
    ) -> GraphNodeModel:
        """Resolve an existing GraphNode by its natural identity key or create a new one.

        Identity Strategy:
            Natural Key: (environment, application_id, component_type, external_component_id).
            Handles nullable application_id using SQL IS NULL matching.

        Update Policy:
            - Preserves original first_seen_at.
            - Advances last_seen_at to observed_at.
            - Updates display_name only if not previously set or when new name provided.
            - Merges safe metadata non-destructively without overwriting existing non-null keys.
        """
        # Extract component identity
        if isinstance(component, ComponentRef):
            comp_id = component.component_id
            comp_type = component.component_type
            disp_name = display_name if display_name is not None else component.name
        elif isinstance(component, dict):
            comp_id = str(component.get("component_id", ""))
            comp_type = str(component.get("component_type", component_type or ""))
            disp_name = display_name if display_name is not None else component.get("name")
        elif isinstance(component, str):
            comp_id = component
            comp_type = component_type or "unknown"
            disp_name = display_name
        else:
            raise ValueError(f"Unsupported component specification: {type(component)}")

        if not comp_id or not comp_type:
            raise ValueError("Both component_id and component_type are required to resolve a GraphNode.")

        # Query existing node using natural key
        stmt = select(GraphNodeModel).where(
            GraphNodeModel.environment == environment,
            GraphNodeModel.component_type == comp_type,
            GraphNodeModel.external_component_id == comp_id,
        )
        if application_id is None:
            stmt = stmt.where(GraphNodeModel.application_id.is_(None))
        else:
            stmt = stmt.where(GraphNodeModel.application_id == application_id)

        node = session.execute(stmt).scalar_one_or_none()
        now = observed_at or datetime.now(timezone.utc)

        if node is not None:
            # Advance last_seen_at
            if node.last_seen_at is None or now > node.last_seen_at:
                node.last_seen_at = now

            # Non-destructive display_name update
            if disp_name and not node.display_name:
                node.display_name = disp_name

            # Non-destructive metadata merge
            clean_meta = _sanitize_metadata(metadata)
            if clean_meta:
                merged = dict(node.node_metadata or {})
                for k, v in clean_meta.items():
                    if k not in merged or merged[k] is None:
                        merged[k] = v
                node.node_metadata = merged

            return node

        # Create new node
        clean_meta = _sanitize_metadata(metadata)
        node = GraphNodeModel(
            node_id=uuid4(),
            component_type=comp_type,
            external_component_id=comp_id,
            display_name=disp_name,
            application_id=application_id,
            environment=environment,
            node_metadata=clean_meta,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(node)
        session.flush()
        return node

    def resolve_edge(
        self,
        session: Session,
        source_node_id: UUID,
        destination_node_id: UUID,
        action: str,
        *,
        status: str = "observed",
        data_labels: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        observed_at: datetime | None = None,
        event_id: UUID | None = None,
        event: FlowEventRecord | None = None,
        environment: str = "development",
        application_id: str | None = None,
    ) -> GraphEdgeModel:
        """Resolve a logical GraphEdge by (source_node_id, destination_node_id, action) or create it.

        Aggregation & Idempotency:
            - If event or event_id is supplied and already linked to this edge in the session
              or database, total_events_count is NOT incremented (duplicate retry protection).
            - Unpersisted events within the active unit of work are tracked via transaction-scoped
              session state that is safely cleared on commit or rollback.
            - If edge is newly created, total_events_count is set to 1.
            - If edge exists and this is a distinct event, total_events_count is incremented by 1.
            - Advances last_observed_at and updates last_status.
            - Merges data_labels without duplicates.
        """
        stmt = (
            select(GraphEdgeModel)
            .where(
                GraphEdgeModel.source_node_id == source_node_id,
                GraphEdgeModel.destination_node_id == destination_node_id,
                GraphEdgeModel.action == action,
            )
            .with_for_update()
        )
        edge = session.execute(stmt).scalar_one_or_none()
        now = observed_at or datetime.now(timezone.utc)
        clean_meta = _sanitize_metadata(metadata)
        incoming_labels = list(dict.fromkeys(data_labels or []))

        # Concurrent-safe creation if edge does not exist
        if edge is None:
            values = {
                "edge_id": uuid4(),
                "source_node_id": source_node_id,
                "destination_node_id": destination_node_id,
                "action": action,
                "first_observed_at": now,
                "last_observed_at": now,
                "total_events_count": 1,
                "last_status": status,
                "data_labels": incoming_labels,
                "edge_metadata": clean_meta,
            }
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                insert_stmt = pg_insert(GraphEdgeModel).values(**values).on_conflict_do_nothing(
                    constraint="uq_graph_edges_connection"
                )
            elif dialect == "sqlite":
                insert_stmt = sqlite_insert(GraphEdgeModel).values(**values).on_conflict_do_nothing(
                    index_elements=["source_node_id", "destination_node_id", "action"]
                )
            else:
                edge = GraphEdgeModel(**values)
                session.add(edge)
                session.flush()
                if event_id is not None:
                    _track_event_in_tx(session, edge.edge_id, event_id)
                return edge
            insert_res = session.execute(insert_stmt)
            edge = session.execute(stmt).scalar_one()

            if insert_res.rowcount == 1:
                # Newly created edge: initial count is 1 for this event
                eff_id = event.event_id if event is not None else event_id
                if eff_id is not None:
                    _track_event_in_tx(session, edge.edge_id, eff_id)
                return edge

        # Existing edge or concurrent edge created by another worker:
        # Determine if this event already contributed to this edge
        is_new_event = True
        if event_id is not None or event is not None:
            resolved_event_id = event.event_id if event is not None else event_id

            # Check 1: Explicit ORM event instance passed directly
            if event is not None and isinstance(event, FlowEventRecord):
                if event.edge_id is not None and edge is not None:
                    if event.edge_id == edge.edge_id:
                        is_new_event = False

            # Check 2: Persisted event in database / identity map / session.new
            if is_new_event and resolved_event_id is not None:
                persisted_stmt = select(FlowEventRecord).where(
                    FlowEventRecord.event_id == resolved_event_id,
                    FlowEventRecord.environment == environment,
                )
                if application_id is not None:
                    persisted_stmt = persisted_stmt.where(FlowEventRecord.application_id == application_id)
                persisted_event = session.execute(persisted_stmt).scalar_one_or_none()
                if persisted_event is None:
                    # Also inspect pending unflushed instances in session.new
                    for obj in session.new:
                        if (
                            isinstance(obj, FlowEventRecord)
                            and obj.event_id == resolved_event_id
                            and obj.environment == environment
                            and (application_id is None or obj.application_id == application_id)
                        ):
                            persisted_event = obj
                            break

                if persisted_event is not None and edge is not None:
                    if persisted_event.edge_id == edge.edge_id:
                        is_new_event = False

            # Check 3: Transaction-scoped tracking for unpersisted events
            if is_new_event and edge is not None and resolved_event_id is not None:
                if _is_event_tracked_in_tx(session, edge.edge_id, resolved_event_id):
                    is_new_event = False

        # Advance last_observed_at
        if edge.last_observed_at is None or now > edge.last_observed_at:
            edge.last_observed_at = now

        # Update latest observed status
        edge.last_status = status

        # Merge data labels without duplicates
        if incoming_labels:
            existing_set = set(edge.data_labels or [])
            new_labels = [lbl for lbl in incoming_labels if lbl not in existing_set]
            if new_labels:
                edge.data_labels = list(edge.data_labels or []) + new_labels

        # Non-destructive metadata merge
        if clean_meta:
            merged = dict(edge.edge_metadata or {})
            for k, v in clean_meta.items():
                if k not in merged or merged[k] is None:
                    merged[k] = v
            edge.edge_metadata = merged

        # Increment counter only for distinct events
        if is_new_event:
            edge.total_events_count += 1
            if event_id is not None:
                _track_event_in_tx(session, edge.edge_id, event_id)

        return edge

    def record_event(
        self,
        session: Session,
        event: FlowEventRecord | FlowEvent,
        *,
        environment: str = "development",
        application_id: str | None = None,
    ) -> tuple[GraphNodeModel, GraphNodeModel, GraphEdgeModel]:
        """Process a flow event, resolving its endpoints and directed edge in the graph.

        Updates event linkage (source_node_id, destination_node_id, edge_id) where applicable.
        """
        # Attach ORM FlowEventRecord to session if not already attached
        if isinstance(event, FlowEventRecord) and event not in session:
            session.add(event)

        source_ref = event.source
        dest_ref = event.destination
        action = event.action
        status = getattr(event, "status", "observed")
        data_labels = getattr(event, "data_labels", [])
        metadata = (
            event.event_metadata
            if isinstance(event, FlowEventRecord)
            else getattr(event, "metadata", None)
        )
        timestamp = getattr(event, "timestamp", None)
        event_id = getattr(event, "event_id", None)

        source_node = self.resolve_node(
            session,
            source_ref,
            environment=environment,
            application_id=application_id,
            observed_at=timestamp,
        )
        dest_node = self.resolve_node(
            session,
            dest_ref,
            environment=environment,
            application_id=application_id,
            observed_at=timestamp,
        )
        edge = self.resolve_edge(
            session,
            source_node.node_id,
            dest_node.node_id,
            action,
            status=status,
            data_labels=data_labels,
            metadata=metadata,
            observed_at=timestamp,
            event_id=event_id,
            event=event if isinstance(event, FlowEventRecord) else None,
            environment=environment,
            application_id=application_id,
        )

        # Link foreign keys if event is an ORM instance or persisted
        if isinstance(event, FlowEventRecord):
            event.source_node_id = source_node.node_id
            event.destination_node_id = dest_node.node_id
            event.edge_id = edge.edge_id
        elif event_id is not None:
            persisted_stmt = select(FlowEventRecord).where(
                FlowEventRecord.event_id == event_id,
                FlowEventRecord.environment == environment,
            )
            if application_id is not None:
                persisted_stmt = persisted_stmt.where(FlowEventRecord.application_id == application_id)
            persisted = session.execute(persisted_stmt).scalar_one_or_none()
            if persisted is None:
                for obj in session.new:
                if (
                    isinstance(obj, FlowEventRecord)
                    and obj.event_id == event_id
                    and obj.environment == environment
                    and (application_id is None or obj.application_id == application_id)
                ):
                        persisted = obj
                        break
            if persisted is not None:
                persisted.source_node_id = source_node.node_id
                persisted.destination_node_id = dest_node.node_id
                persisted.edge_id = edge.edge_id

        return source_node, dest_node, edge

    def load_graph(
        self,
        session: Session,
        *,
        environment: str | None = None,
    ) -> GraphResponse:
        """Query and return graph nodes and edges as a GraphResponse schema.

        If environment is provided, filters nodes and edges matching that environment.
        """
        stmt_nodes = select(GraphNodeModel)
        if environment is not None:
            stmt_nodes = stmt_nodes.where(GraphNodeModel.environment == environment)

        nodes_orm = session.execute(stmt_nodes).scalars().all()
        nodes_schema = [GraphNodeSchema.model_validate(n) for n in nodes_orm]

        if environment is not None:
            stmt_edges = (
                select(GraphEdgeModel)
                .join(GraphNodeModel, GraphEdgeModel.source_node_id == GraphNodeModel.node_id)
                .where(GraphNodeModel.environment == environment)
            )
        else:
            stmt_edges = select(GraphEdgeModel)

        edges_orm = session.execute(stmt_edges).scalars().all()
        edges_schema = [GraphEdgeSchema.model_validate(e) for e in edges_orm]

        return GraphResponse(
            nodes=nodes_schema,
            edges=edges_schema,
            environment=environment,
            generated_at=datetime.now(timezone.utc),
        )
