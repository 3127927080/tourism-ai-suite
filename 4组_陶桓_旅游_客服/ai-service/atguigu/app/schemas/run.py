from typing import Any, Literal

from pydantic import BaseModel, Field


class CurrentMessage(BaseModel):
    message_id: str
    type: Literal["text", "object"]
    content: dict[str, Any]


class HistoryMessage(CurrentMessage):
    role: Literal["user", "ai", "human"]


class AgentRunRequest(BaseModel):
    conversation_id: str
    turn_id: str
    messages: list[CurrentMessage] = Field(min_length=1)
    history: list[HistoryMessage] = Field(default_factory=list)
    # 多轮任务状态（不透明 JSON）：由 Customer Service 传入当前会话的任务状态
    task_state: dict[str, Any] | None = None
