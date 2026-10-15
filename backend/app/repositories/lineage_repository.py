from collections.abc import Sequence
from datetime import timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai_operator.schemas import OperatorScope
from app.models.flow_event import FlowEventRecord
from app.models.lineage_record import LineageRecord as LineageRecordModel
from app.schemas.flow_event import ComponentRef, FlowEvent
from app.schemas.lineage import LineageRecordView


class LineageRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_event(self, event_id: UUID, scope: OperatorScope) -> FlowEvent | None:
        record = self._session.scalar(
            select(FlowEventRecord).where(
                FlowEventRecord.event_id == event_id,
                *self._scope_filters(scope),
            )
        )
        return self._to_schema(record) if record else None

    def get_events_by_ids(
        self, event_ids: Sequence[UUID], scope: OperatorScope, limit: int
    ) -> list[FlowEvent]:
        if not event_ids or limit < 1:
            return []
        records = self._session.scalars(
            select(FlowEventRecord)
            .where(
                FlowEventRecord.event_id.in_(event_ids),
                *self._scope_filters(scope),
            )
            .order_by(FlowEventRecord.timestamp, FlowEventRecord.event_id)
            .limit(limit)
        ).all()
        return [self._to_schema(record) for record in records]

    def get_children(
        self,
        parent_event_ids: Sequence[UUID],
        scope: OperatorScope,
        limit: int,
    ) -> list[FlowEvent]:
        if not parent_event_ids or limit < 1:
            return []
        records = self._session.scalars(
            select(FlowEventRecord)
            .where(
                FlowEventRecord.parent_event_id.in_(parent_event_ids),
                *self._scope_filters(scope),
            )
            .order_by(FlowEventRecord.timestamp, FlowEventRecord.event_id)
            .limit(limit)
        ).all()
        return [self._to_schema(record) for record in records]

    def has_children(self, parent_event_ids: Sequence[UUID], scope: OperatorScope) -> bool:
        if not parent_event_ids:
            return False
        return (
            self._session.scalar(
                select(FlowEventRecord.event_id)
                .where(
                    FlowEventRecord.parent_event_id.in_(parent_event_ids),
                    *self._scope_filters(scope),
                )
                .limit(1)
            )
            is not None
        )

    def list_flow_events(
        self, flow_id: UUID, scope: OperatorScope, limit: int, offset: int
    ) -> list[FlowEvent]:
        records = self._session.scalars(
            select(FlowEventRecord)
            .where(
                FlowEventRecord.flow_id == flow_id,
                *self._scope_filters(scope),
            )
            .order_by(FlowEventRecord.timestamp, FlowEventRecord.event_id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [self._to_schema(record) for record in records]

    def list_component_events(
        self,
        component_id: str,
        direction: str,
        scope: OperatorScope,
        limit: int,
        offset: int,
    ) -> list[FlowEvent]:
        component_filter = {
            "upstream": FlowEventRecord.destination_component_id == component_id,
            "downstream": FlowEventRecord.source_component_id == component_id,
            "both": or_(
                FlowEventRecord.source_component_id == component_id,
                FlowEventRecord.destination_component_id == component_id,
            ),
        }[direction]
        records = self._session.scalars(
            select(FlowEventRecord)
            .where(component_filter, *self._scope_filters(scope))
            .order_by(FlowEventRecord.timestamp, FlowEventRecord.event_id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [self._to_schema(record) for record in records]

    def list_lineage_records(
        self,
        event_ids: Sequence[UUID],
        scope: OperatorScope,
    ) -> list[LineageRecordView]:
        """Return only evidence records for the requested events and exact scope."""
        if not event_ids:
            return []
        records = self._session.scalars(
            select(LineageRecordModel)
            .where(
                LineageRecordModel.originating_event_id.in_(event_ids),
                LineageRecordModel.application_id == scope.application_id,
                LineageRecordModel.environment == scope.environment,
            )
            .order_by(LineageRecordModel.recorded_at, LineageRecordModel.record_id)
        ).all()
        result: list[LineageRecordView] = []
        for record in records:
            metadata = {
                key: value
                for key, value in (record.transformation_metadata or {}).items()
                if not (isinstance(key, str) and key.startswith("_"))
            }
            result.append(
                LineageRecordView.model_validate(
                    {
                        "record_id": record.record_id,
                        "flow_id": record.flow_id,
                        "application_id": record.application_id,
                        "environment": record.environment,
                        "originating_event_id": record.originating_event_id,
                        "parent_event_id": record.parent_event_id,
                        "source_node_id": record.source_node_id,
                        "destination_node_id": record.destination_node_id,
                        "lineage_classification": record.lineage_classification,
                        "evidence_source": record.evidence_source,
                        "action": record.action,
                        "data_labels": list(record.data_labels or []),
                        "transformation_metadata": metadata,
                        "depth": record.depth,
                        "recorded_at": record.recorded_at,
                    }
                )
            )
        return result

    def save_event(self, event: FlowEvent) -> bool:
        existing = self._session.scalar(
            select(FlowEventRecord).where(
                FlowEventRecord.event_id == event.event_id,
                FlowEventRecord.application_id == event.application_id,
                FlowEventRecord.environment == event.environment,
            )
        )
        if existing:
            if self._to_schema(existing) != event:
                raise ValueError("An event with this ID already exists with different content.")
            return False

        self._session.add(
            FlowEventRecord(
                event_id=event.event_id,
                application_id=event.application_id,
                environment=event.environment,
                flow_id=event.flow_id,
                correlation_id=event.correlation_id,
                parent_event_id=event.parent_event_id,
                timestamp=event.timestamp,
                source_component_id=event.source.component_id,
                source_component_type=event.source.component_type,
                source_component_name=event.source.name,
                destination_component_id=event.destination.component_id,
                destination_component_type=event.destination.component_type,
                destination_component_name=event.destination.name,
                action=event.action,
                data_labels=event.data_labels,
                metadata_json=event.metadata,
                status=event.status,
                duration_ms=event.duration_ms,
            )
        )
        self._session.flush()
        return True

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    @staticmethod
    def _scope_filters(scope: OperatorScope) -> tuple[object, object]:
        return (
            FlowEventRecord.application_id == scope.application_id,
            FlowEventRecord.environment == scope.environment,
        )

    @staticmethod
    def _to_schema(record: FlowEventRecord) -> FlowEvent:
        timestamp = record.timestamp
        if timestamp.utcoffset() is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        metadata = dict(record.event_metadata or {})
        parent_event_id = record.parent_event_id
        if parent_event_id is None and metadata.get("_unresolved_parent_id") is not None:
            try:
                parent_event_id = UUID(str(metadata["_unresolved_parent_id"]))
            except (ValueError, TypeError, AttributeError):
                parent_event_id = None
        metadata = {key: value for key, value in metadata.items() if not str(key).startswith("_")}
        return FlowEvent(
            event_id=record.event_id,
            application_id=record.application_id,
            environment=record.environment,
            flow_id=record.flow_id,
            correlation_id=record.correlation_id,
            parent_event_id=parent_event_id,
            timestamp=timestamp,
            source=ComponentRef(
                component_id=record.source_component_id,
                component_type=record.source_component_type,
                name=record.source_component_name,
            ),
            destination=ComponentRef(
                component_id=record.destination_component_id,
                component_type=record.destination_component_type,
                name=record.destination_component_name,
            ),
            action=record.action,
            data_labels=record.data_labels,
            metadata=metadata,
            status=record.status,
            duration_ms=record.duration_ms,
        )
