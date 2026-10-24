"""TaskStateReducer 单元测试（可用 pytest，也可直接 python 运行）。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from atguigu.agent.harness.tasks.reducer import TaskStateReducer
from atguigu.agent.harness.tasks.schema import TaskAction, TaskType
from atguigu.agent.harness.tasks.state import MAX_HISTORY, MAX_STACK_DEPTH

NOW = datetime(2026, 10, 22, tzinfo=UTC)
REDUCER = TaskStateReducer()


def _update(action, task_type=None, slots=None):
    return SimpleNamespace(action=action, task_type=task_type, slots=slots or {}, reason=None)


def _start_refund(slots=None):
    return REDUCER.reduce(
        None,
        _update(TaskAction.START, TaskType.REFUND_REQUEST, slots),
        NOW,
    )


def test_start_creates_active_with_missing_slots():
    state = _start_refund({"order_id": "ORD_SANYA_001"})
    active = state["active"]
    assert active["task_type"] == "refund_request"
    assert active["status"] == "COLLECTING"
    assert active["slots"]["order_id"] == "ORD_SANYA_001"
    assert active["missing_slots"] == ["refund_reason", "refund_type"]
    assert state["history"][-1]["action"] == "START"


def test_update_slots_merges_and_recomputes_missing():
    state = _start_refund({"order_id": "ORD_SANYA_001"})
    state = REDUCER.reduce(
        state,
        _update(TaskAction.UPDATE_SLOTS, slots={"refund_reason": "行程取消"}),
        NOW,
    )
    assert state["active"]["slots"]["refund_reason"] == "行程取消"
    assert state["active"]["missing_slots"] == ["refund_type"]


def test_invalid_slot_not_written():
    state = _start_refund()
    state = REDUCER.reduce(
        state,
        _update(TaskAction.UPDATE_SLOTS, slots={"order_id": "NOT-AN-ORDER"}),
        NOW,
    )
    assert "order_id" not in state["active"]["slots"]


def test_switch_pushes_active_to_stack():
    state = _start_refund({"order_id": "ORD_SANYA_001"})
    state = REDUCER.reduce(
        state,
        _update(TaskAction.SWITCH, TaskType.ORDER_QUERY, {"order_id": "ORD_YUNNAN_001"}),
        NOW,
    )
    assert state["active"]["task_type"] == "order_query"
    assert len(state["stack"]) == 1
    assert state["stack"][0]["task_type"] == "refund_request"
    assert state["stack"][0]["status"] == "SUSPENDED"


def test_complete_auto_resumes_stack():
    state = _start_refund({"order_id": "ORD_SANYA_001"})
    state = REDUCER.reduce(
        state,
        _update(TaskAction.SWITCH, TaskType.ORDER_QUERY, {"order_id": "ORD_YUNNAN_001"}),
        NOW,
    )
    state = REDUCER.reduce(state, _update(TaskAction.COMPLETE), NOW)
    assert state["active"]["task_type"] == "refund_request"
    assert state["stack"] == []
    assert state["active"]["missing_slots"] == ["refund_reason", "refund_type"]


def test_cancel_clears_active_but_keeps_stack():
    state = _start_refund({"order_id": "ORD_SANYA_001"})
    state = REDUCER.reduce(
        state,
        _update(TaskAction.SWITCH, TaskType.ORDER_QUERY, {"order_id": "ORD_YUNNAN_001"}),
        NOW,
    )
    state = REDUCER.reduce(state, _update(TaskAction.CANCEL), NOW)
    assert state["active"] is None
    assert len(state["stack"]) == 1


def test_resume_pops_stack():
    state = _start_refund({"order_id": "ORD_SANYA_001"})
    state = REDUCER.reduce(
        state,
        _update(TaskAction.SWITCH, TaskType.ORDER_QUERY, {"order_id": "ORD_YUNNAN_001"}),
        NOW,
    )
    state["active"] = None
    state = REDUCER.reduce(state, _update(TaskAction.RESUME), NOW)
    assert state["active"]["task_type"] == "refund_request"
    assert state["stack"] == []


def test_cancel_all_clears_active_and_stack():
    state = _start_refund({"order_id": "ORD_SANYA_001"})
    state = REDUCER.reduce(
        state,
        _update(TaskAction.SWITCH, TaskType.ORDER_QUERY, {"order_id": "ORD_YUNNAN_001"}),
        NOW,
    )
    state = REDUCER.reduce(state, _update(TaskAction.CANCEL_ALL), NOW)
    assert state["active"] is None
    assert state["stack"] == []


def test_complete_without_active_is_noop():
    state = REDUCER.reduce(None, _update(TaskAction.COMPLETE), NOW)
    assert state["active"] is None


def test_start_without_task_type_is_noop():
    state = REDUCER.reduce(None, _update(TaskAction.START, None), NOW)
    assert state["active"] is None


def test_stack_depth_capped():
    state = None
    for index in range(MAX_STACK_DEPTH + 2):
        state = REDUCER.reduce(
            state,
            _update(TaskAction.SWITCH, TaskType.ORDER_QUERY, {"order_id": f"ORD_CITY_{index}"}),
            NOW,
        )
    assert len(state["stack"]) <= MAX_STACK_DEPTH


def test_history_capped():
    state = _start_refund({"order_id": "ORD_SANYA_001"})
    for index in range(MAX_HISTORY + 5):
        state = REDUCER.reduce(
            state,
            _update(TaskAction.UPDATE_SLOTS, slots={"refund_reason": f"原因{index}"}),
            NOW + timedelta(seconds=index),
        )
    assert len(state["history"]) <= MAX_HISTORY


def test_repeat_start_same_type_resumes_without_duplicate():
    state = _start_refund({"order_id": "ORD_SANYA_001"})
    state = REDUCER.reduce(
        state,
        _update(TaskAction.SWITCH, TaskType.ORDER_QUERY, {"order_id": "ORD_YUNNAN_001"}),
        NOW,
    )
    assert [s["task_type"] for s in state["stack"]] == ["refund_request"]
    # 模型错误地用 START 重新发起同类型任务 -> 应视为恢复，不得重复入栈
    state = REDUCER.reduce(
        state,
        _update(TaskAction.START, TaskType.REFUND_REQUEST, {"refund_reason": "行程取消"}),
        NOW,
    )
    assert state["active"]["task_type"] == "refund_request"
    assert [s["task_type"] for s in state["stack"]] == ["order_query"]
    assert state["history"][-1]["action"] == "RESUME"


def test_complete_if_ready_completes_when_all_slots_filled():
    state = _start_refund(
        {"order_id": "ORD_SANYA_001", "refund_reason": "行程取消", "refund_type": "全额退款"}
    )
    assert state["active"]["missing_slots"] == []
    completed = REDUCER.complete_if_ready(state)
    assert completed["active"] is None


def test_complete_if_ready_skips_when_missing():
    state = _start_refund({"order_id": "ORD_SANYA_001"})
    unchanged = REDUCER.complete_if_ready(state)
    assert unchanged["active"]["task_type"] == "refund_request"


def _run_all():
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    _run_all()
