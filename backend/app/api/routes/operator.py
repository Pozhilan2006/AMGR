from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import ValidationError

from app.ai_operator.providers.base import (
    ProviderConfigurationError,
    ProviderUnavailableError,
    UnusableProviderResponse,
)
from app.ai_operator.schemas import (
    OperatorChatRequest,
    OperatorChatResponse,
    OperatorIdentity,
    OperatorScope,
)
from app.ai_operator.service import OperatorService, get_operator_service

router = APIRouter()


def get_operator_identity(request: Request) -> OperatorIdentity:
    identity = getattr(request.state, "operator_identity", None)
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required for the AI operator.",
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


def error_response(
    response: Response,
    request: OperatorChatRequest,
    status_code: int,
    message: str,
) -> OperatorChatResponse:
    response.status_code = status_code
    return OperatorChatResponse(
        conversation_id=request.conversation_id or uuid4(),
        assistant_message=message,
        response_type="error",
    )


@router.post("/chat", response_model=OperatorChatResponse)
async def chat(
    request: OperatorChatRequest,
    response: Response,
    identity: OperatorIdentity = Depends(get_operator_identity),
    service: OperatorService = Depends(get_operator_service),
) -> OperatorChatResponse:
    scope = resolve_scope(request.scope, identity)
    try:
        return await service.respond(request, scope)
    except ProviderConfigurationError:
        return error_response(
            response,
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The AI operator is not configured. Contact an administrator.",
        )
    except ProviderUnavailableError:
        return error_response(
            response,
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The AI provider is temporarily unavailable.",
        )
    except UnusableProviderResponse:
        return error_response(
            response,
            request,
            status.HTTP_502_BAD_GATEWAY,
            "The AI provider returned an unusable response.",
        )