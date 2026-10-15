from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai_operator.schemas import OperatorScope
from app.models.flow_event import FlowEventRecord
from app.schemas.graph_queries import GraphEdge, GraphNeighborEntry, GraphNode, GraphSnapshotResponse


class GraphRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_snapshot(
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
        stmt = select(FlowEventRecord).where(
            FlowEventRecord.application_id == scope.application_id,
            FlowEventRecord.environment == scope.environment,
        )

        if component_type:
            stmt = stmt.where(
                or_(
                    FlowEventRecord.source_component_type == component_type,
                    FlowEventRecord.destination_component_type == component_type,
                )
            )
        if flow_id is not None:
            stmt = stmt.where(FlowEventRecord.flow_id == flow_id)
        if start is not None:
            stmt = stmt.where(FlowEventRecord.timestamp >= start)
        if end is not None:
            stmt = stmt.where(FlowEventRecord.timestamp <= end)
        if action:
            stmt = stmt.where(FlowEventRecord.action == action)
        if data_label:
            stmt = stmt.where(FlowEventRecord.data_labels.contains([data_label]))

        stmt = stmt.order_by(FlowEventRecord.timestamp.asc(), FlowEventRecord.event_id.asc())
        records = self._session.scalars(stmt.limit(limit).offset(offset)).all()

        nodes_by_id: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        event_ids: set[str] = set()

        for record in records:
            source_id = record.source_component_id
            destination_id = record.destination_component_id
            source_key = ("component", source_id)
            dest_key = ("component", destination_id)

            if source_id not in nodes_by_id:
                nodes_by_id[source_id] = GraphNode(
                    id=source_id,
                    component={
                        "component_id": source_id,
                        "component_type": record.source_component_type,
                        "name": record.source_component_name,
                    },
                    label=record.source_component_name or source_id,
                    component_type=record.source_component_type,
                    metadata={"source": True},
                )
            if destination_id not in nodes_by_id:
                nodes_by_id[destination_id] = GraphNode(
                    id=destination_id,
                    component={
                        "component_id": destination_id,
                        "component_type": record.destination_component_type,
                        "name": record.destination_component_name,
                    },
                    label=record.destination_component_name or destination_id,
                    component_type=record.destination_component_type,
                    metadata={"destination": True},
                )

            edge_id = f"edge:{source_id}->{destination_id}:{record.event_id}"
            event_ids.add(str(record.event_id))
            edges.append(
                GraphEdge(
                    id=edge_id,
                    source=source_id,
                    target=destination_id,
                    action=record.action,
                    metadata={
                        "event_id": str(record.event_id),
                        "flow_id": str(record.flow_id),
                        "status": record.status,
                        "data_labels": record.data_labels,
                    },
                )
            )

        return GraphSnapshotResponse(
            nodes=list(nodes_by_id.values()),
            edges=edges,
            generated_at=datetime.utcnow(),
            truncated=len(records) >= limit,
            total_nodes=len(nodes_by_id),
            total_edges=len(edges),
        )

    def neighborhood(
        self,
        *,
        component_id: str,
        scope: OperatorScope,
        direction: str = "both",
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[GraphNeighborEntry], dict[str, object]]:
        directions = ["incoming", "outgoing"] if direction == "both" else [direction]
        entries: list[GraphNeighborEntry] = []
        metadata: dict[str, object] = {"component_id": component_id, "direction": direction}

        for item in directions:
            if item == "incoming":
                records = self._session.scalars(
                    select(FlowEventRecord)
                    .where(
                        FlowEventRecord.destination_component_id == component_id,
                        FlowEventRecord.application_id == scope.application_id,
                        FlowEventRecord.environment == scope.environment,
                    )
                    .order_by(FlowEventRecord.timestamp.asc(), FlowEventRecord.event_id.asc())
                    .offset(offset)
                    .limit(limit)
                ).all()
                for record in records:
                    entries.append(
                        GraphNeighborEntry(
                            component_id=record.source_component_id,
                            component_type=record.source_component_type,
                            relationship="incoming",
                            event_id=str(record.event_id),
                            flow_id=str(record.flow_id),
                            action=record.action,
                        )
                    )
            else:
                records = self._session.scalars(
                    select(FlowEventRecord)
                    .where(
                        FlowEventRecord.source_component_id == component_id,
                        FlowEventRecord.application_id == scope.application_id,
                        FlowEventRecord.environment == scope.environment,
                    )
                    .order_by(FlowEventRecord.timestamp.asc(), FlowEventRecord.event_id.asc())
                    .offset(offset)
                    .limit(limit)
                ).all()
                for record in records:
                    entries.append(
                        GraphNeighborEntry(
                            component_id=record.destination_component_id,
                            component_type=record.destination_component_type,
                            relationship="outgoing",
                            event_id=str(record.event_id),
                            flow_id=str(record.flow_id),
                            action=record.action,
                        )
                    )

        return entries, metadata

    def find_paths(
        self,
        *,
        source_component_id: str,
        destination_component_id: str,
        scope: OperatorScope,
        max_depth: int = 2,
        limit: int = 10,
    ) -> list[list[str]]:
        adjacency: dict[str, list[str]] = {}
        records = self._session.scalars(
            select(FlowEventRecord).where(
                FlowEventRecord.application_id == scope.application_id,
                FlowEventRecord.environment == scope.environment,
            )
        ).all()
        for record in records:
            adjacency.setdefault(record.source_component_id, []).append(record.destination_component_id)

        results: list[list[str]] = []
        stack: list[tuple[str, list[str]]] = [(source_component_id, [source_component_id])]
        seen: set[tuple[str, tuple[str, ...]]] = set()

        while stack and len(results) < limit:
            current, path = stack.pop()
            if len(path) - 1 >= max_depth:
                continue
            if current == destination_component_id and len(path) > 1:
                results.append(path)
                continue
            for neighbor in adjacency.get(current, []):
                if neighbor in path:
                    continue
                candidate = path + [neighbor]
                key = (current, tuple(candidate))
                if key in seen:
                    continue
                seen.add(key)
                stack.append((neighbor, candidate))

        return results[:limit]
