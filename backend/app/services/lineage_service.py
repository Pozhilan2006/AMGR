from uuid import UUID

from app.ai_operator.schemas import OperatorScope
from app.repositories.lineage_repository import LineageRepository
from app.schemas.flow_event import FlowEvent
from app.schemas.lineage import (
    LineageDirection,
    LineageEdge,
    LineageNode,
    LineageResponse,
    LineageTraversal,
)


class LineageService:
    def __init__(self, repository: LineageRepository) -> None:
        self._repository = repository

    def resolve_event_lineage(
        self,
        event_id: UUID,
        *,
        scope: OperatorScope,
        direction: LineageDirection,
        max_depth: int,
        limit: int,
    ) -> LineageResponse | None:
        if not 1 <= max_depth <= 8:
            raise ValueError("max_depth must be between 1 and 8.")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        root = self._repository.get_event(event_id, scope)
        if root is None:
            return None

        events = {root.event_id: root}
        frontiers: dict[str, list[FlowEvent]] = {}
        visited: dict[str, set[UUID]] = {}
        directions = ("upstream", "downstream") if direction == "both" else (direction,)
        for item in directions:
            frontiers[item] = [root]
            visited[item] = {root.event_id}

        unresolved_parent_ids: set[UUID] = set()
        truncation_reasons: set[str] = set()

        for _ in range(max_depth):
            for item in directions:
                frontier = frontiers[item]
                if not frontier:
                    continue

                if item == "upstream":
                    parent_ids = list(
                        dict.fromkeys(
                            event.parent_event_id
                            for event in frontier
                            if event.parent_event_id is not None
                        )
                    )
                    candidates = self._repository.get_events_by_ids(
                        parent_ids, scope, limit + 1
                    )
                    available_ids = {event.event_id for event in candidates}
                    unresolved_parent_ids.update(
                        parent_id for parent_id in parent_ids if parent_id not in available_ids
                    )
                else:
                    candidates = self._repository.get_children(
                        [event.event_id for event in frontier], scope, limit + 1
                    )

                next_frontier: list[FlowEvent] = []
                for candidate in candidates:
                    if candidate.event_id in visited[item]:
                        continue
                    if candidate.event_id not in events and len(events) >= limit:
                        truncation_reasons.add("result_limit")
                        continue
                    visited[item].add(candidate.event_id)
                    events[candidate.event_id] = candidate
                    next_frontier.append(candidate)
                frontiers[item] = next_frontier

            if len(events) >= limit:
                for item in directions:
                    frontier = frontiers[item]
                    if not frontier:
                        continue
                    if item == "upstream":
                        unseen_parent_ids = [
                            event.parent_event_id
                            for event in frontier
                            if event.parent_event_id is not None
                            and event.parent_event_id not in events
                        ]
                        if unseen_parent_ids:
                            truncation_reasons.add("result_limit")
                    elif self._repository.has_children(
                        [event.event_id for event in frontier], scope
                    ):
                        truncation_reasons.add("result_limit")
                break

        for item in directions:
            frontier = frontiers[item]
            if not frontier:
                continue
            if item == "upstream" and any(
                event.parent_event_id is not None
                and event.parent_event_id not in events
                for event in frontier
            ):
                truncation_reasons.add("depth_limit")
            if item == "downstream" and self._repository.has_children(
                [event.event_id for event in frontier], scope
            ):
                truncation_reasons.add("depth_limit")

        ordered_events = sorted(
            events.values(), key=lambda event: (event.timestamp, str(event.event_id))
        )
        response_unresolved = unresolved_parent_ids | {
            event.parent_event_id
            for event in ordered_events
            if event.parent_event_id is not None and event.parent_event_id not in events
        }
        return self._response(
            ordered_events,
            scope=scope,
            direction=direction,
            limit=limit,
            max_depth=max_depth,
            unresolved_parent_ids=response_unresolved,
            truncation_reasons=truncation_reasons,
        )

    def resolve_flow_lineage(
        self,
        flow_id: UUID,
        *,
        scope: OperatorScope,
        limit: int,
        offset: int,
    ) -> LineageResponse:
        self._validate_page(limit, offset)
        page = self._repository.list_flow_events(flow_id, scope, limit + 1, offset)
        truncated = len(page) > limit
        events = page[:limit]
        unresolved = {
            event.parent_event_id
            for event in events
            if event.parent_event_id is not None
            and event.parent_event_id not in {item.event_id for item in events}
        }
        return self._response(
            events,
            scope=scope,
            direction="both",
            limit=limit,
            max_depth=None,
            offset=offset,
            next_offset=offset + limit if truncated else None,
            unresolved_parent_ids=unresolved,
            truncation_reasons={"page_limit"} if truncated else set(),
        )

    def resolve_component_lineage(
        self,
        component_id: str,
        *,
        scope: OperatorScope,
        direction: LineageDirection,
        limit: int,
        offset: int,
    ) -> LineageResponse:
        self._validate_page(limit, offset)
        page = self._repository.list_component_events(
            component_id, direction, scope, limit + 1, offset
        )
        truncated = len(page) > limit
        events = page[:limit]
        unresolved = {
            event.parent_event_id
            for event in events
            if event.parent_event_id is not None
            and event.parent_event_id not in {item.event_id for item in events}
        }
        return self._response(
            events,
            scope=scope,
            direction=direction,
            limit=limit,
            max_depth=None,
            offset=offset,
            next_offset=offset + limit if truncated else None,
            unresolved_parent_ids=unresolved,
            truncation_reasons={"page_limit"} if truncated else set(),
        )

    @staticmethod
    def _validate_page(limit: int, offset: int) -> None:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        if offset < 0:
            raise ValueError("offset must be nonnegative.")

    @staticmethod
    def _response(
        events: list[FlowEvent],
        *,
        scope: OperatorScope,
        direction: LineageDirection,
        limit: int,
        max_depth: int | None,
        unresolved_parent_ids: set[UUID],
        truncation_reasons: set[str],
        offset: int = 0,
        next_offset: int | None = None,
    ) -> LineageResponse:
        nodes: dict[tuple[str, str], LineageNode] = {}
        edges: list[LineageEdge] = []
        event_ids = {event.event_id for event in events}

        for event in events:
            event_id = str(event.event_id)
            nodes[("event", event_id)] = LineageNode(
                node_type="event", node_id=event_id, label=event.action
            )
            source = event.source
            destination = event.destination
            nodes[("component", source.component_id)] = LineageNode(
                node_type="component",
                node_id=source.component_id,
                label=source.name or source.component_id,
                component_type=source.component_type,
            )
            nodes[("component", destination.component_id)] = LineageNode(
                node_type="component",
                node_id=destination.component_id,
                label=destination.name or destination.component_id,
                component_type=destination.component_type,
            )
            edges.append(
                LineageEdge(
                    source_type="component",
                    source_id=source.component_id,
                    target_type="component",
                    target_id=destination.component_id,
                    relationship_type="observed_interaction",
                    evidence_type="recorded_event_components",
                    event_id=event_id,
                )
            )
            if event.parent_event_id in event_ids:
                edges.append(
                    LineageEdge(
                        source_type="event",
                        source_id=str(event.parent_event_id),
                        target_type="event",
                        target_id=event_id,
                        relationship_type="explicit_parent_event",
                        evidence_type="recorded_parent_event_id",
                        event_id=event_id,
                    )
                )

        normalized_reasons = sorted(
            truncation_reasons,
            key=("depth_limit", "result_limit", "page_limit").index,
        )
        return LineageResponse(
            events=events,
            nodes=list(nodes.values()),
            edges=edges,
            unresolved_parent_event_ids=sorted(map(str, unresolved_parent_ids)),
            completeness="incomplete" if unresolved_parent_ids or normalized_reasons else "unknown",
            truncated=bool(normalized_reasons),
            truncation_reasons=normalized_reasons,
            traversal=LineageTraversal(
                direction=direction,
                max_depth=max_depth,
                limit=limit,
                offset=offset,
                next_offset=next_offset,
            ),
            scope=scope,
        )
