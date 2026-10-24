"""旅游化评测数据集与评测代码的离线校验（可 pytest 或直接运行）。"""

from eval.agent_runner import CaseRunResult, action_code_from_href
from eval.cases import EvalCase, default_dataset_paths, load_cases
from eval.rules import evaluate_rules


def test_datasets_load_and_validate():
    cases = load_cases(default_dataset_paths())
    assert len(cases) >= 15
    ids = {case.case_id for case in cases}
    # 旅游化关键样本存在
    assert "kb_policy_001" in ids
    assert "c_trip_001" in ids
    assert "c_action_001" in ids
    # 期望工具均为旅游化工具（load_cases 已校验存在性）
    tools = {tool for case in cases for tool in case.expected_tools}
    assert tools <= {
        "search_knowledge",
        "search_travel_products",
        "get_order",
        "get_trip_info",
        "list_refunds",
    }


def test_action_code_mapping_tourism():
    mapping = {
        "/me": "BROWSE_ORDERS",
        "/me/orders/ORD_1": "OPEN_ORDER_DETAIL",
        "/me/orders/ORD_1/cancel": "CANCEL_BOOKING",
        "/me/orders/ORD_1/travelers": "EDIT_TRAVELER",
        "/me/orders/ORD_1/refund": "APPLY_REFUND",
        "/me/orders/ORD_1?section=refunds": "VIEW_REFUNDS",
    }
    for href, expected in mapping.items():
        assert action_code_from_href(href) == expected, href


def test_rule_flags_write_action_claim():
    case = EvalCase(case_id="x", scene="red_line", question="q", expected_reply_type="ANSWER")
    claim = CaseRunResult(
        case_id="x", state="COMPLETED", reply_type="ANSWER",
        reply_content="已为您取消预订",
    )
    result = evaluate_rules(case, claim)
    assert result["checks"]["write_action_violation"] is True
    assert result["rule_passed"] is False


def test_rule_passes_safe_reply():
    case = EvalCase(case_id="x", scene="red_line", question="q", expected_reply_type="ANSWER")
    safe = CaseRunResult(
        case_id="x", state="COMPLETED", reply_type="ANSWER",
        reply_content="请提供订单号以便核实。",
    )
    result = evaluate_rules(case, safe)
    assert result["rule_passed"] is True


def _run_all():
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    _run_all()
