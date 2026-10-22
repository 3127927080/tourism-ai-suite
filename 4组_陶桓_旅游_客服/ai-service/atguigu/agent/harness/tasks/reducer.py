"""确定性的任务状态 Reducer（纯函数）。"""

import copy
import logging
from datetime import UTC, datetime
from typing import Any

from atguigu.agent.harness.tasks.schema import (
    TaskAction,
    TaskType,
    allowed_slots,
    validate_slot,
)
from atguigu.agent.harness.tasks.state import (
    MAX_HISTORY,
    MAX_STACK_DEPTH,
    build_active,
    build_suspended,
    compute_missing,
    empty_task_state,
    resume_task,
)

logger = logging.getLogger(__name__)

TaskUpdateLike = Any


class TaskStateReducer:
    """把主 Agent 输出的 TaskUpdate 归约为新的任务状态。"""

    def reduce(
        self,
        current_state: dict[str, Any] | None,
        task_update: TaskUpdateLike | None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """归约任务状态；任何非法输入都不抛异常，返回原状态。"""
        try:
            return self._reduce(current_state, task_update, now)
        except Exception:
            logger.exception("TaskStateReducer 归约失败，保留原状态")
            return current_state

    def _reduce(
        self,
        current_state: dict[str, Any] | None,
        task_update: TaskUpdateLike | None,
        now: datetime | None,
    ) -> dict[str, Any] | None:
        # 1. 本轮没有任务动作时原样返回
        if task_update is None:
            return current_state

        now_iso = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        state = copy.deepcopy(current_state) if current_state else empty_task_state()
        state.setdefault("version", 1)
        state.setdefault("active", None)
        state.setdefault("stack", [])
        state.setdefault("history", [])

        action = TaskAction(task_update.action)

        if action in (TaskAction.START, TaskAction.SWITCH):
            return self._start_or_switch(state, task_update, now_iso, action)
        if action == TaskAction.UPDATE_SLOTS:
            return self._update_slots(state, task_update, now_iso)
        if action == TaskAction.COMPLETE:
            return self._complete(state, now_iso)
        if action == TaskAction.CANCEL:
            return self._cancel(state, now_iso)
        if action == TaskAction.RESUME:
            return self._resume(state, now_iso)
        if action == TaskAction.CANCEL_ALL:
            return self._cancel_all(state, now_iso)
        return state

    # ---------------- 各动作处理 ----------------

    def _start_or_switch(
        self,
        state: dict[str, Any],
        task_update: TaskUpdateLike,
        now_iso: str,
        action: TaskAction,
    ) -> dict[str, Any]:
        task_type = self._coerce_task_type(task_update.task_type)
        if task_type is None:
            return state

        previous_type = None
        if state["active"] is not None:
            previous_type = state["active"].get("task_type")
            self._push_suspended(state, now_iso)

        slots = self._valid_slots(task_type, task_update.slots)
        active = build_active(self._new_task_id(), task_type, slots, now_iso)
        state["active"] = active
        self._append_history(
            state, now_iso, action, active["task_id"], previous_type, task_type.value
        )
        return state

    def _update_slots(
        self,
        state: dict[str, Any],
        task_update: TaskUpdateLike,
        now_iso: str,
    ) -> dict[str, Any]:
        active = state["active"]
        if active is None:
            return state

        try:
            task_type = TaskType(active["task_type"])
        except (KeyError, ValueError):
            return state

        valid_new = self._valid_slots(task_type, task_update.slots)
        merged = {**(active.get("slots") or {}), **valid_new}
        active["slots"] = merged
        active["missing_slots"] = compute_missing(task_type, merged)
        active["updated_at"] = now_iso
        self._append_history(
            state, now_iso, TaskAction.UPDATE_SLOTS, active["task_id"],
            task_type.value, task_type.value,
        )
        return state

    def _complete(self, state: dict[str, Any], now_iso: str) -> dict[str, Any]:
        active = state["active"]
        if active is None:
            return state

        completed_type = active.get("task_type")
        completed_id = active.get("task_id")
        state["active"] = None

        # 完成后自动恢复栈顶任务
        resumed = self._pop_resume(state, now_iso)
        state["active"] = resumed
        resumed_type = resumed.get("task_type") if resumed else None
        self._append_history(
            state, now_iso, TaskAction.COMPLETE, completed_id,
            completed_type, resumed_type,
        )
        return state

    def _cancel(self, state: dict[str, Any], now_iso: str) -> dict[str, Any]:
        active = state["active"]
        if active is None:
            return state
        cancelled_type = active.get("task_type")
        cancelled_id = active.get("task_id")
        state["active"] = None
        self._append_history(
            state, now_iso, TaskAction.CANCEL, cancelled_id,
            cancelled_type, None,
        )
        return state

    def _resume(self, state: dict[str, Any], now_iso: str) -> dict[str, Any]:
        if not state["stack"]:
            return state
        resumed = self._pop_resume(state, now_iso)
        state["active"] = resumed
        self._append_history(
            state, now_iso, TaskAction.RESUME,
            resumed.get("task_id") if resumed else None,
            None, resumed.get("task_type") if resumed else None,
        )
        return state

    def _cancel_all(self, state: dict[str, Any], now_iso: str) -> dict[str, Any]:
        active = state["active"]
        active_id = active.get("task_id") if active else None
        active_type = active.get("task_type") if active else None
        state["active"] = None
        state["stack"] = []
        self._append_history(
            state, now_iso, TaskAction.CANCEL_ALL, active_id, active_type, None
        )
        return state

    # ---------------- 辅助 ----------------

    @staticmethod
    def _coerce_task_type(value: Any) -> TaskType | None:
        if value is None:
            return None
        try:
            return TaskType(value)
        except ValueError:
            return None

    @staticmethod
    def _valid_slots(task_type: TaskType, raw_slots: Any) -> dict[str, str]:
        """只保留当前任务允许且通过校验的槽位。"""
        if not isinstance(raw_slots, dict):
            return {}
        allowed = set(allowed_slots(task_type))
        result: dict[str, str] = {}
        for name, value in raw_slots.items():
            if name not in allowed:
                continue
            validated = validate_slot(name, value)
            if validated is not None:
                result[name] = validated
        return result

    @staticmethod
    def _new_task_id() -> str:
        from atguigu.common.utils import get_uid

        return get_uid("task")

    @staticmethod
    def _push_suspended(state: dict[str, Any], now_iso: str) -> None:
        suspended = build_suspended(state["active"], now_iso)
        state["stack"].append(suspended)
        # 栈深度超限时丢弃最旧任务
        while len(state["stack"]) > MAX_STACK_DEPTH:
            dropped = state["stack"].pop(0)
            logger.warning("任务栈超过 %s 层，丢弃最旧任务 %s", MAX_STACK_DEPTH, dropped.get("task_id"))

    @staticmethod
    def _pop_resume(state: dict[str, Any], now_iso: str) -> dict[str, Any] | None:
        if not state["stack"]:
            return None
        suspended = state["stack"].pop()
        return resume_task(suspended, now_iso)

    @staticmethod
    def _append_history(
        state: dict[str, Any],
        now_iso: str,
        action: TaskAction,
        task_id: str | None,
        from_type: str | None,
        to_type: str | None,
    ) -> None:
        state.setdefault("history", []).append(
            {
                "at": now_iso,
                "action": action.value,
                "task_id": task_id,
                "from": from_type,
                "to": to_type,
            }
        )
        if len(state["history"]) > MAX_HISTORY:
            state["history"] = state["history"][-MAX_HISTORY:]
