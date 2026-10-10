from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("")
def get_graph() -> None:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Flow graph queries are not implemented.",
    )