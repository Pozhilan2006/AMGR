"""Event ingestion service for AMGR flow events."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

try:
    from backend.app.models.flow_event import FlowEventRecord
    from backend.app.models.lineage_record import LineageRecord
    from backend.app.schemas.flow_event import (
        FlowEvent,
        FlowEventIngestionResponse,
        IngestionStatus,
    )
    from backend.app.services.graph_service import GraphService, _sanitize_metadata
except ImportError:
    from app.models.flow_event import FlowEventRecord
    from app.models.lineage_record import LineageRecord
    from app.schemas.flow_event import (
        FlowEvent,
        FlowEventIngestionResponse,
        IngestionStatus,
    )
    from app.services.graph_service import GraphService, _sanitize_metadata

UNRESOLVED_PARENT_KEY = "_unresolved_parent_id"


class EventPayloadConflictError(Exception):
    """Raised when an incoming event_id matches an existing persisted event with conflicting payload attributes."""

    def __init__(self, message: str, event_id: UUID) -> None:
        super().__init__(message)
        self.message = message
        self.event_id = event_id
        self.code = "EVENT_PAYLOAD_CONFLICT"


class InvalidParentEventError(ValueError):
    """Base domain exception for invalid parent event references."""

    def __init__(self, message: str, event_id: UUID, parent_event_id: UUID) -> None:
        super().__init__(message)
        self.message = message
        self.event_id = event_id
        self.parent_event_id = parent_event_id


class SelfParentReferenceError(InvalidParentEventError):
    """Raised when an event references itself as its parent."""


class CyclicParentReferenceError(InvalidParentEventError):
    """Raised when a parent reference would create or encounter a cyclic ancestor chain."""


class CrossFlowParentError(InvalidParentEventError):
    """Raised when an event references a parent event belonging to a different flow."""


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

    # Check parent_event_id accounting for unresolved parent references in metadata
    persisted_parent = persisted.parent_event_id
    if persisted_parent is None and persisted.event_metadata:
        unresolved = persisted.event_metadata.get(UNRESOLVED_PARENT_KEY)
        if unresolved is not None:
            try:
                persisted_parent = UUID(str(unresolved))
            except (ValueError, AttributeError):
                persisted_parent = None

    if incoming.parent_event_id != persisted_parent:
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
    clean_p = {
        k: v
        for k, v in (persisted.event_metadata or {}).items()
        if k != UNRESOLVED_PARENT_KEY
    }
    if clean_in != clean_p:
        return False

    return True


def _flow_id_to_advisory_lock_keys(flow_id: UUID) -> tuple[int, int]:
    """Derive two 32-bit signed integers from a UUID for PostgreSQL pg_advisory_xact_lock(int, int).

    The 128-bit UUID integer is split into high and low 64-bit halves and folded with XOR
    into two 32-bit words, then converted to signed 32-bit integers [-2^31, 2^31 - 1]
    matching PostgreSQL's 4-byte signed 'integer' type.

    Note on Collision Probability:
        Mapping 128-bit UUIDs into 64 bits of advisory lock space has a theoretical collision
        probability (birthday bound). A collision causes two distinct flows to briefly queue
        sequentially rather than executing concurrently; it does not compromise data integrity.
    """
    u_int = flow_id.int
    high_64 = (u_int >> 64) & 0xFFFFFFFFFFFFFFFF
    low_64 = u_int & 0xFFFFFFFFFFFFFFFF

    word1 = ((high_64 >> 32) ^ (low_64 >> 32)) & 0xFFFFFFFF
    word2 = (high_64 ^ low_64) & 0xFFFFFFFF

    # Convert unsigned 32-bit to signed 32-bit integer for PostgreSQL
    if word1 >= 0x80000000:
        word1 -= 0x100000000
    if word2 >= 0x80000000:
        word2 -= 0x100000000

    return word1, word2


def _acquire_flow_advisory_lock(session: Session, flow_id: UUID) -> None:
    """Acquire a transaction-level PostgreSQL advisory lock scoped to the given flow_id.

    Serializes concurrent ingestion requests for the same flow, eliminating phantom-arrival
    races between concurrent parent and child events without blocking concurrent requests for
    different flows.
    Automatically released by PostgreSQL upon transaction commit or rollback.
    Preserves normal execution on non-PostgreSQL binds (e.g. SQLite test harnesses).
    """
    bind = getattr(session, "bind", None)
    if bind is None:
        try:
            bind = session.get_bind()
        except Exception:
            bind = None

    if bind is not None and getattr(bind.dialect, "name", "") == "postgresql":
        key1, key2 = _flow_id_to_advisory_lock_keys(flow_id)
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key1, :key2)"),
            {"key1": key1, "key2": key2},
        )


def _would_create_cycle(
    child_id: UUID,
    proposed_parent_id: UUID,
    session: Session,
) -> bool:
    """Detect whether linking child_id -> proposed_parent_id would create or encounter a cyclic ancestor chain.

    Returns True if:
        1. child_id == proposed_parent_id (self-parent reference).
        2. child_id is encountered while traversing the ancestor chain from proposed_parent_id (cycle created).
        3. An already-visited event is encountered while traversing proposed_parent_id's ancestor chain
           (the proposed parent chain already contains a cycle).
    """
    if child_id == proposed_parent_id:
        return True

    visited: set[UUID] = set()
    curr_id: UUID | None = proposed_parent_id

    while curr_id is not None:
        if curr_id == child_id:
            return True
        if curr_id in visited:
            # The proposed parent chain itself already contains a cycle
            return True
        visited.add(curr_id)

        curr_event = session.get(FlowEventRecord, curr_id)
        if curr_event is None:
            for obj in session.new:
                if isinstance(obj, FlowEventRecord) and obj.event_id == curr_id:
                    curr_event = obj
                    break

        if curr_event is None:
            break

        next_parent = curr_event.parent_event_id
        if next_parent is None and curr_event.event_metadata:
            unresolved = curr_event.event_metadata.get(UNRESOLVED_PARENT_KEY)
            if unresolved is not None:
                try:
                    next_parent = UUID(str(unresolved))
                except (ValueError, AttributeError):
                    next_parent = None

        curr_id = next_parent

    return False


def _find_lineage_record(
    session: Session,
    originating_event_id: UUID,
) -> LineageRecord | None:
    """Find the LineageRecord for a given originating_event_id across database and active session."""
    stmt = (
        select(LineageRecord)
        .where(LineageRecord.originating_event_id == originating_event_id)
        .with_for_update()
    )
    record = session.execute(stmt).scalar_one_or_none()
    if record is not None:
        return record

    for obj in session.new:
        if isinstance(obj, LineageRecord) and obj.originating_event_id == originating_event_id:
            return obj

    return None


def _propagate_lineage_to_descendants(
    session: Session,
    ancestor_event_id: UUID,
    ancestor_lineage: LineageRecord,
) -> None:
    """Propagate updated lineage classification and depth to downstream child events."""
    desc_stmt = (
        select(FlowEventRecord)
        .where(
            FlowEventRecord.flow_id == ancestor_lineage.flow_id,
            FlowEventRecord.parent_event_id == ancestor_event_id,
        )
        .with_for_update()
    )
    descendants = list(session.execute(desc_stmt).scalars().all())

    for obj in session.new:
        if (
            isinstance(obj, FlowEventRecord)
            and obj.flow_id == ancestor_lineage.flow_id
            and obj.parent_event_id == ancestor_event_id
            and obj not in descendants
        ):
            descendants.append(obj)

    for desc in descendants:
        desc_lineage = _find_lineage_record(session, desc.event_id)
        if desc_lineage is not None:
            # Classification: if ancestor is complete, descendant becomes explicitly_derived
            if ancestor_lineage.lineage_classification in ("directly_observed", "explicitly_derived"):
                desc_lineage.lineage_classification = "explicitly_derived"
            else:
                desc_lineage.lineage_classification = "unknown_or_incomplete"

            # Depth: ancestor.depth + 1 or None
            if ancestor_lineage.depth is not None:
                desc_lineage.depth = ancestor_lineage.depth + 1
            else:
                desc_lineage.depth = None

            # Recurse down the causal DAG
            _propagate_lineage_to_descendants(session, desc.event_id, desc_lineage)


def _resolve_pending_children(
    session: Session,
    parent_record: FlowEventRecord,
    parent_lineage: LineageRecord,
) -> None:
    """Resolve pending child events and lineage records whose parent arrived out-of-order,
    ensuring classification and depth are updated accurately without remaining stale.
    """
    stmt = (
        select(FlowEventRecord)
        .where(
            FlowEventRecord.flow_id == parent_record.flow_id,
            FlowEventRecord.parent_event_id.is_(None),
        )
        .with_for_update()
    )
    candidates = list(session.execute(stmt).scalars().all())

    for obj in session.new:
        if (
            isinstance(obj, FlowEventRecord)
            and obj.flow_id == parent_record.flow_id
            and obj.parent_event_id is None
            and obj not in candidates
        ):
            candidates.append(obj)

    target_parent_id_str = str(parent_record.event_id)

    for child in candidates:
        if child.event_id == parent_record.event_id:
            continue

        child_meta = child.event_metadata or {}
        unresolved_id = child_meta.get(UNRESOLVED_PARENT_KEY)
        if unresolved_id is None or str(unresolved_id) != target_parent_id_str:
            continue

        # Prevent cycles before linking (rejects both cycles involving child and cyclic parent chains)
        if _would_create_cycle(child.event_id, parent_record.event_id, session):
            continue

        # Link child to parent and remove unresolved marker
        child.parent_event_id = parent_record.event_id
        updated_child_meta = dict(child_meta)
        updated_child_meta.pop(UNRESOLVED_PARENT_KEY, None)
        child.event_metadata = updated_child_meta

        # Update child LineageRecord
        child_lineage = _find_lineage_record(session, child.event_id)
        if child_lineage is not None:
            child_lineage.parent_event_id = parent_record.event_id

            # Classification: if parent provenance is confirmed, upgrade to explicitly_derived;
            # if parent provenance remains incomplete upstream, preserve unknown_or_incomplete
            if parent_lineage.lineage_classification in ("directly_observed", "explicitly_derived"):
                child_lineage.lineage_classification = "explicitly_derived"
            else:
                child_lineage.lineage_classification = "unknown_or_incomplete"

            # Depth: parent_lineage.depth + 1 if known, else explicitly None
            if parent_lineage.depth is not None:
                child_lineage.depth = parent_lineage.depth + 1
            else:
                child_lineage.depth = None

            updated_lmeta = dict(child_lineage.transformation_metadata or {})
            updated_lmeta.pop(UNRESOLVED_PARENT_KEY, None)
            child_lineage.transformation_metadata = updated_lmeta

            # Cascade updates to descendants of this child so downstream depth/classification do not remain stale
            _propagate_lineage_to_descendants(session, child.event_id, child_lineage)


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
        """Validate, normalize, persist, and record a FlowEvent into the flow graph and lineage DAG.

        Outcomes:
            - New event successfully processed: Returns status ACCEPTED.
            - Identical previously committed event: Returns status ALREADY_PROCESSED.
            - Duplicate event_id with conflicting content: Raises EventPayloadConflictError.
            - Self-parent reference: Raises SelfParentReferenceError.
            - Cyclic parent reference: Raises CyclicParentReferenceError.
            - Cross-flow parent reference: Raises CrossFlowParentError.
            - Out-of-order parent: Retains unresolved parent reference safely, marks lineage as unknown_or_incomplete.
            - Arrival of parent: Resolves pending child references and updates lineage classification/depth.
            - Database primary-key collision on concurrent submission:
              Rolls back the failed transaction block, loads the committed record,
              and returns ALREADY_PROCESSED for identical content or raises EventPayloadConflictError for conflicting content.
        """
        # Step 0: Acquire PostgreSQL transaction-scoped advisory lock for this flow_id
        # Must occur before duplicate-event checks, parent lookups, and graph updates
        _acquire_flow_advisory_lock(session, event.flow_id)

        # Step 1: Pre-validation of parent references (self-parent and cycle prevention)
        if event.parent_event_id is not None:
            if event.parent_event_id == event.event_id:
                raise SelfParentReferenceError(
                    f"Self-referential parent_event_id is not permitted: event '{event.event_id}' cannot reference itself as parent.",
                    event_id=event.event_id,
                    parent_event_id=event.parent_event_id,
                )
            if _would_create_cycle(event.event_id, event.parent_event_id, session):
                raise CyclicParentReferenceError(
                    f"Cyclic parent reference detected: event '{event.event_id}' cannot reference ancestor '{event.parent_event_id}'.",
                    event_id=event.event_id,
                    parent_event_id=event.parent_event_id,
                )

        # Step 2: Check if event is already persisted in the database (idempotent retry)
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

        # Step 3: Resolve parent event if present
        effective_parent_id: UUID | None = None
        parent_record: FlowEventRecord | None = None
        is_parent_unresolved = False

        if event.parent_event_id is not None:
            parent_record = session.get(FlowEventRecord, event.parent_event_id)
            if parent_record is None:
                for obj in session.new:
                    if isinstance(obj, FlowEventRecord) and obj.event_id == event.parent_event_id:
                        parent_record = obj
                        break

            if parent_record is not None:
                if parent_record.flow_id != event.flow_id:
                    raise CrossFlowParentError(
                        f"Cross-flow parent reference is not permitted: parent event '{event.parent_event_id}' belongs to flow '{parent_record.flow_id}', not '{event.flow_id}'.",
                        event_id=event.event_id,
                        parent_event_id=event.parent_event_id,
                    )
                effective_parent_id = parent_record.event_id
            else:
                effective_parent_id = None
                is_parent_unresolved = True

        # Step 4: Sanitize and prepare event metadata
        clean_metadata = _sanitize_metadata(event.metadata)
        if is_parent_unresolved and event.parent_event_id is not None:
            clean_metadata[UNRESOLVED_PARENT_KEY] = str(event.parent_event_id)

        # Step 5: Instantiate FlowEventRecord from incoming FlowEvent
        record = FlowEventRecord(
            event_id=event.event_id,
            flow_id=event.flow_id,
            correlation_id=event.correlation_id,
            parent_event_id=effective_parent_id,
            timestamp=event.timestamp,
            source=event.source.model_dump(),
            destination=event.destination.model_dump(),
            action=event.action,
            data_labels=list(event.data_labels or []),
            event_metadata=clean_metadata,
            status=event.status,
            duration_ms=event.duration_ms,
            environment=environment,
        )

        # Step 6: Determine Lineage classification, depth, and safe transformation metadata
        lineage_meta = _sanitize_metadata(event.metadata)
        if event.parent_event_id is None:
            classification = "directly_observed"
            depth = 0
        elif not is_parent_unresolved and parent_record is not None:
            parent_lineage = _find_lineage_record(session, parent_record.event_id)
            if parent_lineage is not None:
                if parent_lineage.lineage_classification in ("directly_observed", "explicitly_derived"):
                    classification = "explicitly_derived"
                else:
                    classification = "unknown_or_incomplete"

                if parent_lineage.depth is not None:
                    depth = parent_lineage.depth + 1
                else:
                    depth = None
            else:
                classification = "explicitly_derived"
                depth = 1
        else:
            classification = "unknown_or_incomplete"
            depth = None
            if event.parent_event_id is not None:
                lineage_meta[UNRESOLVED_PARENT_KEY] = str(event.parent_event_id)

        # Step 7: Persist and record into graph inside a savepoint to isolate potential PK collision
        lineage_record: LineageRecord | None = None
        try:
            with session.begin_nested():
                source_node, dest_node, edge = self.graph_service.record_event(
                    session,
                    record,
                    environment=environment,
                    application_id=application_id,
                )

                lineage_record = LineageRecord(
                    record_id=uuid4(),
                    flow_id=record.flow_id,
                    originating_event_id=record.event_id,
                    parent_event_id=record.parent_event_id,
                    source_node_id=record.source_node_id,
                    destination_node_id=record.destination_node_id,
                    lineage_classification=classification,
                    evidence_source="sdk_runtime_event",
                    action=record.action,
                    data_labels=list(record.data_labels or []),
                    transformation_metadata=lineage_meta,
                    depth=depth,
                    recorded_at=datetime.now(timezone.utc),
                )
                session.add(lineage_record)

                # Resolve any pending children that were waiting for this event as their parent
                _resolve_pending_children(session, record, lineage_record)

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
            if lineage_record is not None and lineage_record in session:
                session.expunge(lineage_record)

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
            # If committed is None, the conflict was not a duplicate event_id.
            # Do not swallow unexpected database errors.
            raise
