from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("")
def list_policies() -> None:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Policy lifecycle is not implemented.",
    )