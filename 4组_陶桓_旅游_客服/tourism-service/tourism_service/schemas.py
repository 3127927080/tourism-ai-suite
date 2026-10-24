from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class DemoLoginRequest(BaseModel):
    user_id: str = "user_001"
    role: Literal["customer", "agent", "admin"] = "customer"


class RefundCreate(BaseModel):
    """退款申请：字段与 design 4.6 对齐。"""

    kind: Literal["full", "partial", "reschedule"] = "full"
    reason: str = Field(min_length=2, max_length=300)


class TicketCreate(BaseModel):
    """投诉 / 售后 / 退款工单。"""

    ticket_type: Literal["after_sale", "complaint", "refund"] = "complaint"
    issue_description: str = Field(min_length=2, max_length=1000)


class TravelerUpdate(BaseModel):
    traveler_info: list[dict] = Field(min_length=1)


def money(value: Decimal) -> float:
    return float(value)
