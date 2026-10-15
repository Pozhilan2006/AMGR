from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.ai_operator.schemas import OperatorScope
from app.repositories.graph_repository import GraphRepository
from app.schemas.graph_queries import GraphNeighborhoodResponse, GraphPathResponse, GraphSnapshotResponse


class GraphService:
    """Graph persistence and traversal layer backed by the persisted flow events."""

    def __init__(self, repository: GraphRepository) -> None:
        self._repository = repository

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
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500.")
        if offset < 0:
            raise ValueError("offset must be non-negative.")
        return self._repository.list_snapshot(
            scope=scope,
            limit=limit,
            offset=offset,
            component_type=component_type,
            flow_id=flow_id,
            start=start,
            end=end,
            action=action,
            data_label=data_label,
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
        if not component_id.strip():
            raise ValueError("component_id is required.")
        if direction not in {"incoming", "outgoing", "both"}:
            raise ValueError("direction must be incoming, outgoing, or both.")
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100.")
        if offset < 0:
            raise ValueError("offset must be non-negative.")

        entries, _ = self._repository.neighborhood(
            component_id=component_id,
            scope=scope,
            direction=direction,
            limit=limit,
            offset=offset,
        )
        return GraphNeighborhoodResponse(
            component_id=component_id,
            neighbors=entries,
            metadata={"direction": direction, "limit": limit, "offset": offset},
            unavailable=False,
            message="Observed component neighbors in the authorized scope.",
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
        if not source_component_id.strip() or not destination_component_id.strip():
            raise ValueError("source_component_id and destination_component_id are required.")
        if max_depth < 1 or max_depth > 8:
            raise ValueError("max_depth must be between 1 and 8.")
        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50.")

        paths = self._repository.find_paths(
            source_component_id=source_component_id,
            destination_component_id=destination_component_id,
            scope=scope,
            max_depth=max_depth,
            limit=limit,
        )
        if not paths:
            return GraphPathResponse(
                source_component_id=source_component_id,
                destination_component_id=destination_component_id,
                paths=[],
                completeness="complete",
                message="No supported component path was found in the authorized scope.",
            )
        return GraphPathResponse(
            source_component_id=source_component_id,
            destination_component_id=destination_component_id,
            paths=[
                {
                    "path_id": f"path:{index}",
                    "nodes": path,
                    "edges": [f"edge:{path[i]}->{path[i + 1]}" for i in range(len(path) - 1)],
                    "evidence": ["recorded_component_interactions"],
                }
                for index, path in enumerate(paths, start=1)
            ],
            completeness="incomplete" if len(paths) >= limit else "complete",
            message="Paths represent recorded component interactions only; they do not prove data movement.",
        )
