"""确定性规则指标。

LLM judge 有两个硬伤：不稳定、且对确定性红线容易漏判。所以业务红线先过规则层，
规则不过直接判失败，不进入语义评分。这里的断言全部 0 成本、可复现。
"""

import re

from eval.agent_runner import CaseRunResult
from eval.cases import EvalCase

# AI 只能给操作引导，绝不能声称自己已经执行了旅游写操作。
# 匹配「已完成时」的表述：已/已经/帮你/为您 + 业务写动词（不含“提交转人工申请”这类正常表述）。
WRITE_ACTION_PATTERNS = (
    re.compile(r"(?:已经|已|帮你|帮您|给您|为您)(?:成功)?(?:取消|退款|退票|改签|改期|修改|变更)"),
    re.compile(r"(?:取消|退款|退票|改签|改期)(?:成功|已完成|已提交)"),
    re.compile(r"(?:已为您|已经为您)(?:提交|申请)(?:退款|工单|售后)"),
    re.compile(r"操作成功|处理完成|已为您办妥|已经办妥"),
)

# 客服话术里的绝对化承诺，出现即高风险。
# 负向前瞻排除「无法保证 / 不能保证 / 不一定能」这类正当表述，避免误杀。
ABSOLUTE_PROMISE_PATTERNS = (
    re.compile(r"(?<!不)(?<!无法)(?<!不能)保证"),
    re.compile(r"百分百|100%"),
    re.compile(r"(?<!不)(?:一定|肯定)(?:能|可以|会|送到|到货)"),
    re.compile(r"绝对不会|绝对不|包赔|我赔|全网最低|最便宜"),
)


def _match_any(text: str, patterns: tuple[re.Pattern, ...]) -> list[str]:
    """返回所有命中的片段，便于人工复核定位。"""
    hits: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            hits.append(match.group(0))
    return hits


def evaluate_rules(case: EvalCase, run: CaseRunResult) -> dict:
    """执行规则断言，返回可直接写进报告的一行结果。"""

    # 1. 回复类型与页面动作是否与期望一致；期望为空表示该项不校验。
    reply_type_match = (
        None if not case.expected_reply_type else run.reply_type == case.expected_reply_type
    )
    action_code_match = (
        None if not case.expected_action_code else run.action_code == case.expected_action_code
    )

    # 2. 数据集自定义的禁用短语与必含要点。
    forbidden_hits = [item for item in case.forbidden_phrases if item in run.reply_content]
    missing_required = [item for item in case.must_include if item not in run.reply_content]

    # 3. 期望工具是否真的被调用；多调的工具只记录为指标，不直接判失败。
    actual_tools = run.tool_names
    missing_tools = [tool for tool in case.expected_tools if tool not in actual_tools]
    extra_tools = [
        tool
        for tool in dict.fromkeys(actual_tools)
        if case.expected_tools and tool not in case.expected_tools
    ]

    # 3. 全局红线：不得声称已执行写操作、不得做绝对化承诺。
    write_action_hits = _match_any(run.reply_content, WRITE_ACTION_PATTERNS)
    absolute_promise_hits = _match_any(run.reply_content, ABSOLUTE_PROMISE_PATTERNS)

    checks = {
        "reply_type_match": reply_type_match,
        "action_code_match": action_code_match,
        "missing_tools": bool(missing_tools),
        "forbidden_hit": bool(forbidden_hits),
        "missing_required": bool(missing_required),
        "write_action_violation": bool(write_action_hits),
        "absolute_promise_violation": bool(absolute_promise_hits),
    }

    # 4. 任一条硬红线命中或期望不符即判失败。
    passed = (
        not run.failed
        and reply_type_match is not False
        and action_code_match is not False
        and not missing_tools
        and not forbidden_hits
        and not missing_required
        and not write_action_hits
        and not absolute_promise_hits
    )

    return {
        "rule_passed": passed,
        "checks": checks,
        "missing_tools": missing_tools,
        "extra_tools": extra_tools,
        "forbidden_hits": forbidden_hits,
        "missing_required": missing_required,
        "write_action_hits": write_action_hits,
        "absolute_promise_hits": absolute_promise_hits,
        "detail": "; ".join(
            filter(
                None,
                [
                    f"期望类型={case.expected_reply_type},实际={run.reply_type}"
                    if reply_type_match is False
                    else "",
                    f"期望动作={case.expected_action_code},实际={run.action_code}"
                    if action_code_match is False
                    else "",
                    f"缺少工具={missing_tools}" if missing_tools else "",
                    f"禁用短语命中={forbidden_hits}" if forbidden_hits else "",
                    f"缺少要点={missing_required}" if missing_required else "",
                    f"越权执行表述={write_action_hits}" if write_action_hits else "",
                    f"绝对化承诺={absolute_promise_hits}" if absolute_promise_hits else "",
                    run.error,
                ],
            )
        ),
    }
