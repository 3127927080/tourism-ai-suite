"""任务类型、槽位与校验规则。"""

import re
from datetime import date
from enum import StrEnum


class TaskType(StrEnum):
    """多轮任务类型。"""

    PRODUCT_CONSULT = "product_consult"
    ORDER_QUERY = "order_query"
    TRIP_INFO = "trip_info"
    REFUND_REQUEST = "refund_request"
    COMPLAINT_TICKET = "complaint_ticket"
    POLICY_QUERY = "policy_query"


class TaskAction(StrEnum):
    """主 Agent 每轮最多输出的一个任务动作。"""

    START = "START"
    SWITCH = "SWITCH"
    UPDATE_SLOTS = "UPDATE_SLOTS"
    COMPLETE = "COMPLETE"
    CANCEL = "CANCEL"
    RESUME = "RESUME"
    CANCEL_ALL = "CANCEL_ALL"


# 每种保留任务的必填/可选槽位
TASK_SLOTS: dict[TaskType, dict[str, tuple[str, ...]]] = {
    TaskType.PRODUCT_CONSULT: {
        "required": ("city", "product_type"),
        "optional": ("keyword", "date"),
    },
    TaskType.ORDER_QUERY: {"required": ("order_id",), "optional": ()},
    TaskType.TRIP_INFO: {"required": ("order_id",), "optional": ()},
    TaskType.REFUND_REQUEST: {
        "required": ("order_id", "refund_reason", "refund_type"),
        "optional": (),
    },
    TaskType.COMPLAINT_TICKET: {
        "required": ("ticket_type", "order_id", "issue_description"),
        "optional": (),
    },
    TaskType.POLICY_QUERY: {"required": ("topic",), "optional": ("city",)},
}

PRODUCT_TYPE_VALUES = {"hotel", "scenic", "ticket", "transfer"}
REFUND_TYPE_VALUES = {"全额退款", "部分退款", "改期"}
TICKET_TYPE_VALUES = {"售后", "投诉", "退款"}

ORDER_ID_PATTERN = re.compile(r"^ORD[_-][A-Z0-9_-]+$", re.IGNORECASE)
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

MAX_SLOT_TEXT_LENGTH = 200


def required_slots(task_type: TaskType) -> tuple[str, ...]:
    """返回任务必填槽位。"""
    return TASK_SLOTS[task_type]["required"]


def allowed_slots(task_type: TaskType) -> tuple[str, ...]:
    """返回任务全部可用槽位（必填 + 可选）。"""
    definition = TASK_SLOTS[task_type]
    return definition["required"] + definition["optional"]


def validate_slot(name: str, value: object) -> str | None:
    """校验单个槽位值，非法时返回 None（不写入）。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_SLOT_TEXT_LENGTH:
        return None

    if name == "order_id":
        return text.upper().replace("-", "_") if ORDER_ID_PATTERN.match(text) else None
    if name == "product_type":
        lowered = text.lower()
        return lowered if lowered in PRODUCT_TYPE_VALUES else None
    if name == "refund_type":
        return text if text in REFUND_TYPE_VALUES else None
    if name == "ticket_type":
        return text if text in TICKET_TYPE_VALUES else None
    if name == "date":
        if not DATE_PATTERN.match(text):
            return None
        try:
            date.fromisoformat(text)
        except ValueError:
            return None
        return text
    # 文本类槽位
    return text


def slot_schema_text() -> str:
    """渲染任务与槽位说明，供 Prompt 注入。"""
    lines: list[str] = []
    for task_type, definition in TASK_SLOTS.items():
        required = "、".join(definition["required"]) or "无"
        optional = "、".join(definition["optional"]) or "无"
        lines.append(f"- {task_type.value}: 必填[{required}] 可选[{optional}]")
    return "\n".join(lines)
