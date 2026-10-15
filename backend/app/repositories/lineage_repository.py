from collections.abc import Sequence
from datetime import timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.ai_operator.schemas import OperatorScope
from app.models.flow_event import FlowEventRecord
from app.schemas.flow_event import ComponentRef, FlowEvent


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
        return FlowEvent(
            event_id=record.event_id,
            application_id=record.application_id,
            environment=record.environment,
            flow_id=record.flow_id,
            correlation_id=record.correlation_id,
            parent_event_id=record.parent_event_id,
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
            metadata=record.metadata_json,
            status=record.status,
            duration_ms=record.duration_ms,
        )
