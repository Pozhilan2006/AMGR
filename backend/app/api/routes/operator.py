from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.dependencies import get_operator_identity, resolve_scope
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