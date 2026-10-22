from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ToolFailureType(StrEnum):
    """定义工具未返回成功结果的原因类型"""

    BUSINESS = "BUSINESS"   #  ToolResult 的success：False(失败成功) 的success：True(成功的成功)
    SERVICE_CALL = "SERVICE_CALL"
    CONTRACT = "CONTRACT"


class ToolFailureCode(StrEnum):
    """定义工具执行层生成的稳定失败结果码"""

    TOOL_CALL_FAILED = "TOOL_CALL_FAILED"
    INVALID_TOOL_RESULT = "INVALID_TOOL_RESULT"


class ToolResult[ResultData](BaseModel):
    """定义业务工具返回给 Agent 的统一结果"""

    success: bool
    code: str
    message: str
    data: ResultData | None = None
    failure_type: ToolFailureType | None = None


class TravelProductData(BaseModel):
    """定义 Agent 查询旅游产品需要的数据（酒店/景区/票务/接送）。"""

    id: str
    name: str
    type: str
    city: str
    price: Decimal = Field(ge=0)
    stock: int = Field(ge=0)
    status: str
    description: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class AvailabilityData(BaseModel):
    """定义 Agent 查询产品余量（房态/余票）需要的数据"""

    product_id: str
    available_count: int = Field(ge=0)
    availability_status: str
    date_range: str | None = None


class OrderItemData(BaseModel):
    """定义 Agent 回答订单产品问题需要的数据"""

    product_id: str
    product_name: str
    product_type: str
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(ge=0)


class OrderData(BaseModel):
    """定义 Agent 查询和校验订单需要的数据"""

    id: str
    user_id: str | None = None
    status: str
    status_label: str | None = None
    total_amount: Decimal = Field(ge=0)
    created_at: datetime
    travel_start_date: date | None = None
    travel_end_date: date | None = None
    items: list[OrderItemData]


class TripEventData(BaseModel):
    """定义出行信息中的一条行程事件"""

    time: str
    description: str


class TripInfoData(BaseModel):
    """定义 Agent 查询订单出行信息需要的数据"""

    order_id: str
    travel_date: date | None = None
    checkin_date: date | None = None
    checkout_date: date | None = None
    departure_time: str | None = None
    arrival_time: str | None = None
    transport_no: str | None = None
    traveler_info: list[dict[str, Any]] = Field(default_factory=list)
    meeting_point: str | None = None
    status: str
    events: list[TripEventData] = Field(default_factory=list)


class RefundData(BaseModel):
    """定义 Agent 查询退款记录需要的数据"""

    id: str
    order_id: str
    kind: str
    reason: str
    status: str
    amount: Decimal | None = None
    created_at: datetime
