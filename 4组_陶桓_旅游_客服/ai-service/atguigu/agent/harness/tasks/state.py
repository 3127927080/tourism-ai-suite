"""任务状态结构定义与工具函数。"""

from typing import Any

from atguigu.agent.harness.tasks.schema import TaskType, required_slots

STATE_VERSION = 1
MAX_STACK_DEPTH = 3
MAX_HISTORY = 20


def empty_task_state() -> dict[str, Any]:
    """返回一个空的任务状态。"""
    return {
        "version": STATE_VERSION,
        "active": None,
        "stack": [],
        "history": [],
    }


def compute_missing(
    task_type: TaskType,
    slots: dict[str, Any],
) -> list[str]:
    """根据任务类型计算仍缺失的必填槽位。"""
    return [
        name
        for name in required_slots(task_type)
        if not slots.get(name)
    ]


def build_active(
    task_id: str,
    task_type: TaskType,
    slots: dict[str, str],
    now_iso: str,
) -> dict[str, Any]:
    """构建一个正在收集的 active 任务。"""
    return {
        "task_id": task_id,
        "task_type": task_type.value,
        "status": "COLLECTING",
        "slots": dict(slots),
        "missing_slots": compute_missing(task_type, slots),
        "created_at": now_iso,
        "updated_at": now_iso,
    }


def build_suspended(
    active: dict[str, Any],
    now_iso: str,
) -> dict[str, Any]:
    """把 active 任务转换成挂起任务。"""
    task_type = TaskType(active["task_type"])
    slots = dict(active.get("slots") or {})
    return {
        "task_id": active["task_id"],
        "task_type": active["task_type"],
        "status": "SUSPENDED",
        "slots": slots,
        "missing_slots": compute_missing(task_type, slots),
        "suspended_at": now_iso,
    }


def resume_task(suspended: dict[str, Any], now_iso: str) -> dict[str, Any]:
    """把挂起任务恢复成 active 任务。"""
    task_type = TaskType(suspended["task_type"])
    slots = dict(suspended.get("slots") or {})
    return {
        "task_id": suspended["task_id"],
        "task_type": suspended["task_type"],
        "status": "COLLECTING",
        "slots": slots,
        "missing_slots": compute_missing(task_type, slots),
        "created_at": suspended.get("suspended_at", now_iso),
        "updated_at": now_iso,
    }
