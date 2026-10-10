from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("")
def list_approvals() -> None:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Approval processing is not implemented.",
    )