from fastapi import HTTPException, Request, status
from pydantic import ValidationError

from app.ai_operator.schemas import OperatorIdentity, OperatorScope


def get_operator_identity(request: Request) -> OperatorIdentity:
    identity = getattr(request.state, "operator_identity", None)
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required for this AMGR operation.",
        )

    try:
        return OperatorIdentity.model_validate(identity)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The authenticated identity is invalid.",
        ) from exc


def resolve_scope(
    requested_scope: OperatorScope | None, identity: OperatorIdentity
) -> OperatorScope:
    if requested_scope is None:
        if len(identity.authorized_scopes) != 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Specify one of the authenticated identity's authorized scopes.",
            )
        return identity.authorized_scopes[0]

    if requested_scope not in identity.authorized_scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The requested scope is not authorized.",
        )
    return requested_scope


def resolve_request_scope(
    request: Request,
    application_id: str | None,
    environment: str | None,
) -> OperatorScope:
    identity = get_operator_identity(request)
    if application_id is None and environment is None:
        return resolve_scope(None, identity)
    if application_id is None or environment is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Both application_id and environment are required when specifying scope.",
        )
    return resolve_scope(
        OperatorScope(application_id=application_id, environment=environment),
        identity,
    )
