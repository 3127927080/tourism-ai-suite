"""
与消息相关的路由
"""
from typing import Annotated

from fastapi import APIRouter, Header

from atguigu.app.dependencies import MessageServiceDep, get_auth_service
from atguigu.app.schemas.message import AcceptUserMessageResponse, ChatMessageRequest

router = APIRouter(tags=["聊天路由"], prefix="/api/v1/chat")


@router.post("/messages", response_model=AcceptUserMessageResponse)
async def accept_user_message(
        chat_message: ChatMessageRequest,
        message_service: MessageServiceDep,
        authorization: Annotated[str | None, Header()] = None
):
    # 权限限制
    authorized_user = get_auth_service().get_authorized_user(authorization, "customer")

    result = await message_service.accept_user_message(chat_message, authorized_user.user_id)

    return  result


@router.get("/history")
async def get_chat_history(
        message_service: MessageServiceDep,
        after_sequence: int | None = None,
        limit: int = 500,
        authorization: Annotated[str | None, Header()] = None
):
    """返回当前用户全部会话的历史消息，支持 after_sequence 游标增量拉取。"""
    authorized_user = get_auth_service().get_authorized_user(authorization, "customer")
    capped_limit = min(max(limit, 1), 1000)
    return await message_service.get_user_history(
        authorized_user.user_id,
        after_sequence,
        capped_limit,
    )