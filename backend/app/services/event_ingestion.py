from sqlalchemy.exc import IntegrityError

from app.ai_operator.schemas import OperatorScope
from app.repositories.lineage_repository import LineageRepository
from app.schemas.flow_event import FlowEvent


class EventIngestionService:
    def __init__(self, repository: LineageRepository) -> None:
        self._repository = repository

    def ingest(self, event: FlowEvent) -> bool:
        try:
            created = self._repository.save_event(event)
            self._repository.commit()
            return created
        except IntegrityError:
            self._repository.rollback()
            existing = self._repository.get_event(
                event.event_id,
                OperatorScope(
                    application_id=event.application_id,
                    environment=event.environment,
                ),
            )
            if existing == event:
                return False
            raise
