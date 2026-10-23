from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atguigu.agent.harness.rules.action import ActionCode
from atguigu.agent.harness.tasks.schema import TaskAction, TaskType


class ReplyType(StrEnum):
    """Agent 本轮生成的回复类型。"""

    ANSWER = "ANSWER"
    CLARIFY = "CLARIFY"
    DECLINE = "DECLINE"
    REQUEST_HANDOFF = "REQUEST_HANDOFF"


class HandoffReason(StrEnum):
    """转人工的业务原因。"""

    USER_REQUESTED = "USER_REQUESTED"
    COMPLAINT = "COMPLAINT"
    RISK_REVIEW = "RISK_REVIEW"


class HandoffRequest(BaseModel):
    """定义 Customer Service 创建人工工单需要的信息。"""

    reason_code: HandoffReason = Field(description="创建人工工单使用的转人工原因")
    summary: str = Field(
        min_length=1,
        max_length=500,
        description="面向人工客服的问题摘要"
    )


class PageActionRequest(BaseModel):
    """定义 Agent 建议用户前往页面完成的操作意图"""
    model_config = ConfigDict(extra="forbid")

    action_code: ActionCode = Field(
        description="服务端允许的页面动作编码"
    )
    resource_id: str | None = Field(
        default=None,
        description="具体订单页面动作使用的订单编号，必须与订单详情工具m成功确认的订单编号一致；订单列表页不提供"
    )

    @model_validator(mode="after")
    def validate_resource_id(self) -> Self:
        """校验页面动作与订单编号的组合关系"""

        # 1. 判断当前请求是否只需要进入订单列表页
        is_order_list = self.action_code == ActionCode.BROWSE_ORDERS

        # 2. 订单列表动作禁止携带具体订单编号
        if is_order_list and self.resource_id is not None:
            raise ValueError("BROWSE_ORDERS 不接受 resource_id")

        # 3. 具体订单动作必须携带订单编号
        if not is_order_list and self.resource_id is None:
            raise ValueError( f"{self.action_code} 必须提供 resource_id")

        # 4. 返回通过字段组合校验的页面动作请求
        return self


class TaskUpdate(BaseModel):
    """定义主 Agent 每轮输出的任务状态变更。"""

    model_config = ConfigDict(extra="forbid")

    action: TaskAction = Field(description="本轮任务动作")
    task_type: TaskType | None = Field(
        default=None,
        description="START/SWITCH 时必填，其余动作可为空"
    )
    slots: dict[str, str] = Field(
        default_factory=dict,
        description="本轮新收集或更新的槽位"
    )
    reason: str | None = Field(default=None, description="动作原因，便于审计")

    @model_validator(mode="after")
    def validate_task_update(self) -> Self:
        """校验任务动作与任务类型的组合关系。"""
        # 1. START/SWITCH 必须提供任务类型
        if (
                self.action in (TaskAction.START, TaskAction.SWITCH)
                and self.task_type is None
        ):
            raise ValueError(f"{self.action} 必须提供 task_type")
        # 2. 返回通过组合校验的任务更新
        return self


class AgentOutput(BaseModel):
    """定义大语言模型必须返回的结构化结果。"""

    reply_type: ReplyType = Field(description="本轮客服回复的业务类型")
    reply_content: str = Field(min_length=1, max_length=4000, description="可以直接展示给用户的完整回复内容")
    handoff_request: HandoffRequest | None = Field(default=None, description="仅请求转人工时提供的工单信息")
    page_action_request: PageActionRequest | None = Field(
        default=None,
        description="仅当用户明确要求前往、打开或查看页面，或者提出必须在页面完成的操作时提供；仅查询业务信息时不得提供"
    )
    task_update: TaskUpdate | None = Field(
        default=None,
        description="本轮对多轮任务状态的动作与槽位更新；没有任务变化时为空"
    )

    @model_validator(mode="before")
    @classmethod
    def sanitize_optional_parts(cls, data):
        """容错：丢弃不合规的可选子结构，避免整轮回复被判结构化失败。"""
        if not isinstance(data, dict):
            return data
        reply_type = data.get("reply_type")

        # 1. 非转人工不得携带工单信息
        if reply_type != ReplyType.REQUEST_HANDOFF and data.get("handoff_request") is not None:
            data["handoff_request"] = None

        # 2. 页面动作：仅 ANSWER；BROWSE 不带资源、其他必须带资源，否则丢弃
        page_action = data.get("page_action_request")
        if page_action is not None:
            code = page_action.get("action_code") if isinstance(page_action, dict) else None
            resource_id = page_action.get("resource_id") if isinstance(page_action, dict) else None
            invalid = (
                reply_type != ReplyType.ANSWER
                or (code == "BROWSE_ORDERS" and resource_id is not None)
                or (code not in (None, "BROWSE_ORDERS") and resource_id is None)
            )
            if invalid:
                data["page_action_request"] = None

        # 3. 任务动作：START/SWITCH 缺少 task_type 时丢弃该子结构
        task_update = data.get("task_update")
        if (
            isinstance(task_update, dict)
            and task_update.get("action") in ("START", "SWITCH")
            and task_update.get("task_type") is None
        ):
            data["task_update"] = None

        return data

    @model_validator(mode="after")
    def validate_output_fields(self) -> Self:
        """校验回复类型与附加请求之间的组合关系"""

        # 1. 判断当前回复是否请求转人工
        is_handoff = self.reply_type == ReplyType.REQUEST_HANDOFF

        # 2. 转人工回复必须携带人工工单信息
        if is_handoff and self.handoff_request is None:
            raise ValueError("REQUEST_HANDOFF 必须提供 handoff_request")

        # 3. 非转人工回复禁止携带人工工单信息
        if not is_handoff and self.handoff_request is not None:
            raise ValueError("只有 REQUEST_HANDOFF 可以提供 handoff_request")

        # 4. 只有正常回答可以请求客户端页面动作
        if (
                self.page_action_request is not None
                and self.reply_type != ReplyType.ANSWER
        ):
            raise ValueError("只有 ANSWER 可以提供 page_action_request")

        # 5. 返回通过字段组合校验的 Agent 输出
        return self
