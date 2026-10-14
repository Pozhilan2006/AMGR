"""Event ingestion service for AMGR flow events."""

from datetime import timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.flow_event import FlowEventRecord
from app.schemas.flow_event import (
    FlowEvent,
    FlowEventIngestionResponse,
    IngestionStatus,
)
from app.services.graph_service import GraphService, _sanitize_metadata


class EventPayloadConflictError(Exception):
    """Raised when an incoming event_id matches an existing persisted event with conflicting payload attributes."""

    def __init__(self, message: str, event_id: UUID) -> None:
        super().__init__(message)
        self.message = message
        self.event_id = event_id
        self.code = "EVENT_PAYLOAD_CONFLICT"


def _events_match(incoming: FlowEvent, persisted: FlowEventRecord) -> bool:
    """Compare an incoming FlowEvent with an existing FlowEventRecord across public contract fields."""
    if incoming.flow_id != persisted.flow_id:
        return False

    if incoming.action != persisted.action:
        return False

    src = incoming.source
    p_src = persisted.source or {}
    if (
        src.component_id != p_src.get("component_id")
        or src.component_type != p_src.get("component_type")
        or src.name != p_src.get("name")
    ):
        return False

    dst = incoming.destination
    p_dst = persisted.destination or {}
    if (
        dst.component_id != p_dst.get("component_id")
        or dst.component_type != p_dst.get("component_type")
        or dst.name != p_dst.get("name")
    ):
        return False

    if incoming.status != persisted.status:
        return False

    if incoming.parent_event_id != persisted.parent_event_id:
        return False

    if incoming.correlation_id != persisted.correlation_id:
        return False

    if (incoming.duration_ms is None) != (persisted.duration_ms is None):
        return False
    if incoming.duration_ms is not None and persisted.duration_ms is not None:
        if abs(incoming.duration_ms - persisted.duration_ms) > 1e-6:
            return False

    if sorted(incoming.data_labels or []) != sorted(persisted.data_labels or []):
        return False

    t_in = incoming.timestamp.astimezone(timezone.utc)
    p_t = persisted.timestamp
    if p_t is None:
        return False
    if p_t.utcoffset() is None:
        p_t = p_t.replace(tzinfo=timezone.utc)
    else:
        p_t = p_t.astimezone(timezone.utc)
    if t_in != p_t:
        return False

    clean_in = _sanitize_metadata(incoming.metadata)
    clean_p = persisted.event_metadata or {}
    if clean_in != clean_p:
        return False

    return True


def _is_flow_event_pk_collision(exc: IntegrityError) -> bool:
    """Determine whether an IntegrityError was caused by a primary key uniqueness conflict on flow_events.

    Inspects PostgreSQL-specific SQLSTATE 23505 and constraint diagnostics where available,
    while maintaining fallback compatibility for non-PostgreSQL drivers and test harnesses.
    """
    orig = getattr(exc, "orig", None)
    if orig is not None:
        sqlstate = (
            getattr(orig, "sqlstate", None)
            or getattr(orig, "pgcode", None)
            or getattr(getattr(orig, "diag", None), "sqlstate", None)
        )
        diag = getattr(orig, "diag", None)
        constraint_name = getattr(diag, "constraint_name", None) if diag else None

        # When running against PostgreSQL with structured diagnostics
        if sqlstate == "23505":
            if constraint_name == "flow_events_pkey":
                return True
            if constraint_name is not None and constraint_name != "flow_events_pkey":
                # Distinct PostgreSQL constraint violation (e.g. graph_edges or unique nodes)
                return False

    # Diagnostic inspection for drivers or mock harnesses without structured diag
    err_msg = str(exc).lower()
    if "flow_events_pkey" in err_msg or "flow_events.event_id" in err_msg:
        return True

    return False


class EventIngestionService:
    """Inbound runtime event processing and graph linkage coordinator."""

    def __init__(self, graph_service: GraphService | None = None) -> None:
        self.graph_service = graph_service or GraphService()
        self._implemented = True

    def ingest(
        self,
        session: Session,
        event: FlowEvent,
        *,
        environment: str = "development",
        application_id: str | None = None,
    ) -> FlowEventIngestionResponse:
        """Validate, normalize, persist, and record a FlowEvent into the flow graph.

        Outcomes:
            - New event successfully processed: Returns status ACCEPTED.
            - Identical previously committed event: Returns status ALREADY_PROCESSED.
            - Duplicate event_id with conflicting content: Raises EventPayloadConflictError.
            - Database primary-key collision on concurrent submission:
              Rolls back the failed transaction block, loads the committed record,
              and returns ALREADY_PROCESSED for identical content or raises EventPayloadConflictError for conflicting content.
        """
        # Step 1: Check if event is already persisted in the database
        existing = session.get(FlowEventRecord, event.event_id)
        if existing is not None:
            if _events_match(event, existing):
                return FlowEventIngestionResponse(
                    event_id=existing.event_id,
                    status=IngestionStatus.ALREADY_PROCESSED,
                    flow_id=existing.flow_id,
                    edge_id=existing.edge_id,
                    source_node_id=existing.source_node_id,
                    destination_node_id=existing.destination_node_id,
                    message="Event already processed (idempotent retry).",
                )
            raise EventPayloadConflictError(
                message=f"Event ID conflict: Payload attributes differ from existing event with ID '{event.event_id}'.",
                event_id=event.event_id,
            )

        # Step 2: Instantiate FlowEventRecord from incoming FlowEvent
        record = FlowEventRecord(
            event_id=event.event_id,
            flow_id=event.flow_id,
            correlation_id=event.correlation_id,
            parent_event_id=event.parent_event_id,
            timestamp=event.timestamp,
            source=event.source.model_dump(),
            destination=event.destination.model_dump(),
            action=event.action,
            data_labels=list(event.data_labels or []),
            event_metadata=_sanitize_metadata(event.metadata),
            status=event.status,
            duration_ms=event.duration_ms,
            environment=environment,
        )

        # Step 3: Persist and record into graph inside a savepoint to isolate potential PK collision
        try:
            with session.begin_nested():
                source_node, dest_node, edge = self.graph_service.record_event(
                    session,
                    record,
                    environment=environment,
                    application_id=application_id,
                )
                session.flush()

            return FlowEventIngestionResponse(
                event_id=record.event_id,
                status=IngestionStatus.ACCEPTED,
                flow_id=record.flow_id,
                edge_id=record.edge_id,
                source_node_id=record.source_node_id,
                destination_node_id=record.destination_node_id,
                message="Event ingested successfully.",
            )
        except IntegrityError as exc:
            # Re-raise integrity errors that are not primary-key collisions on flow_events
            # (e.g. concurrent graph edge creation collisions or foreign key violations)
            if not _is_flow_event_pk_collision(exc):
                raise

            # Concurrent race: another worker may have committed the exact same event_id.
            # The begin_nested context manager rolled back the savepoint, leaving the session clean.
            if record in session:
                session.expunge(record)
            committed = session.get(FlowEventRecord, event.event_id)
            if committed is not None:
                if _events_match(event, committed):
                    return FlowEventIngestionResponse(
                        event_id=committed.event_id,
                        status=IngestionStatus.ALREADY_PROCESSED,
                        flow_id=committed.flow_id,
                        edge_id=committed.edge_id,
                        source_node_id=committed.source_node_id,
                        destination_node_id=committed.destination_node_id,
                        message="Event already processed (concurrent duplicate).",
                    )
                raise EventPayloadConflictError(
                    message=f"Event ID conflict: Payload attributes differ from concurrently committed event '{event.event_id}'.",
                    event_id=event.event_id,
                )
            # If committed is None, the conflict was not a duplicate event_id (e.g. edge race deferred to Task 9B).
            # Do not swallow unexpected database errors.
            raise
