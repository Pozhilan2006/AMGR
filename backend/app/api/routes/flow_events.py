from fastapi import APIRouter, HTTPException, status

from app.schemas.flow_event import FlowEvent

router = APIRouter()


@router.post("", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def ingest_flow_event(_event: FlowEvent) -> None:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Flow event ingestion is not implemented.",
    )