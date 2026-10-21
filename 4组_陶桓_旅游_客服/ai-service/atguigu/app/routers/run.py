from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Header, HTTPException, status

from atguigu.agent.harness.errors import (
    AgentRunForbiddenError,
    AgentRunNotFoundError,
    AgentRunStateError,
)
from atguigu.app.dependencies import (
    AgentRunCoordinatorDep,
    get_auth_service,
    require_internal_service,
)
from atguigu.app.schemas.run import AgentRunRequest

router = APIRouter(prefix="/internal/v1/agent", tags=["AI Run"])


def _raise_run_http_error(exc: Exception) -> NoReturn:
    """把 AgentRun 语义异常转换成接口状态码。"""
    if isinstance(exc, AgentRunNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc)
        ) from exc
    if isinstance(exc, AgentRunForbiddenError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc)
        ) from exc
    if isinstance(exc, AgentRunStateError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc)
        ) from exc
    raise exc


@router.post("/runs")
async def start_run(
        request: AgentRunRequest,
        _: Annotated[None, Depends(require_internal_service)],
        agent_coordinator: AgentRunCoordinatorDep,
        authorization: Annotated[str | None, Header()] = None,

) -> dict:
    auth_service = get_auth_service()
    current_user = auth_service.get_authorized_user(
        authorization,
        "customer"
    )
    access_token = auth_service.get_bearer_token(authorization)
    return await agent_coordinator.start_run(current_user.user_id, access_token, request)


@router.post("/runs/{run_id}/confirm")
async def confirm_run(
        run_id: str,
        agent_coordinator: AgentRunCoordinatorDep,
        _: Annotated[None, Depends(require_internal_service)],
        authorization: Annotated[str | None, Header()] = None
) -> dict:
    current_user = get_auth_service().get_authorized_user(
        authorization,
        "customer"
    )
    try:
        return await agent_coordinator.confirm_run(current_user.user_id, run_id)
    except (AgentRunNotFoundError, AgentRunForbiddenError, AgentRunStateError) as exc:
        _raise_run_http_error(exc)


@router.post(
    "/runs/{run_id}/cancel",
    status_code=204  # 接口正常返回 响应体没有任何的数据（比200更精准）
)
async def cancel_run(
        run_id: str,
        agent_coordinator: AgentRunCoordinatorDep,
        _: Annotated[None, Depends(require_internal_service)],
        authorization: Annotated[str | None, Header()] = None
):
    current_user = get_auth_service().get_authorized_user(
        authorization,
        "customer"
    )
    try:
        await agent_coordinator.cancel_run(current_user.user_id, run_id)
    except (AgentRunNotFoundError, AgentRunForbiddenError) as exc:
        _raise_run_http_error(exc)
