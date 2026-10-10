from fastapi import APIRouter, HTTPException, status

from app.schemas.authorization import AuthorizationRequest

router = APIRouter()


@router.post("/decisions")
def authorize(_request: AuthorizationRequest) -> None:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Authorization and policy evaluation are not implemented.",
    )