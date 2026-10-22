"""在进程内跑一次完整的 AgentRun，并抽取评估需要的证据。

为什么不直接调 AI Service 的 HTTP 接口：
1. 需要拿到 run_id，再从 agent_tool_calls 读回本次真实消费的知识片段；
2. HTTP 接口在 DECISION_PREPARED 状态下不返回结果体，评估会丢数据。

这里复用的就是线上同一套 AgentRunCoordinator / AgentExecutor / AgentOutputValidator，
保证评估跑的是真实链路，而不是另写一条简化实现。
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt

from atguigu.agent.harness.run.coordinator import AgentRunCoordinator
from atguigu.agent.harness.run.executor import AgentExecutor
from atguigu.agent.harness.tools.snapshot import ToolCallReader
from atguigu.agent.harness.validator.output import AgentOutputValidator
from atguigu.app.repositories.run import AgentRunRepository
from atguigu.app.repositories.tool import ToolCallRepository
from atguigu.app.schemas.run import AgentRunRequest, CurrentMessage, HistoryMessage
from atguigu.infrastructure.db import session_factory
from atguigu.models.models import AgentRun, AgentRunState
from eval.cases import EvalCase
from eval.config import EvalSettings


@dataclass
class CaseRunResult:
    """一条样本的执行结果，既是报告数据也是指标输入。"""

    case_id: str
    run_id: str = ""
    state: str = ""
    reply_type: str = ""
    reply_content: str = ""
    action_code: str = ""
    action_href: str = ""
    handoff_reason: str = ""
    contexts: list[str] = field(default_factory=list)
    context_ids: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    latency_ms: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""

    @property
    def failed(self) -> bool:
        """执行失败或状态异常时不允许再算 RAGAS 指标。"""
        return bool(self.error) or self.state in {
            AgentRunState.FAILED.value,
            "",
        }

    @property
    def tool_names(self) -> list[str]:
        """按调用顺序返回本次 Run 实际执行的工具名。"""
        return [item["tool_name"] for item in self.tool_calls]


def build_eval_token(settings: EvalSettings, user_id: str) -> str:
    """按项目共享密钥签发一个短期 customer token。

    Customer Service 转发给 AI Service 的就是这种令牌，评估时自行签发即可。
    订单类样本需要旅游数据服务里真实存在的用户，否则工具调用会被业务侧拒绝。
    """
    now = datetime.now(UTC)
    payload = {
        "user_id": user_id,
        "role": "customer",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=2)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _build_request(case: EvalCase) -> AgentRunRequest:
    """把一条 CSV 样本转换成内部 AgentRunRequest。"""
    history = [
        HistoryMessage(
            message_id=f"h-{index}",
            type="text",
            content={"text": item["content"]},
            role=item["role"],
        )
        for index, item in enumerate(case.history, 1)
    ]
    return AgentRunRequest(
        # 使用带前缀的假会话/轮次编号，避免与线上真实数据混淆。
        conversation_id=f"eval-conv-{case.case_id}",
        turn_id=f"eval-turn-{uuid4().hex[:12]}",
        messages=[
            CurrentMessage(
                message_id=f"m-{uuid4().hex[:8]}",
                type="text",
                content={"text": case.question},
            )
        ],
        history=history,
    )


def action_code_from_href(href: str) -> str:
    """从服务端生成的页面路径反推动作编码。

    线上把可信页面动作转换成 PageAction(label/description/href)，动作编码不在结果里，
    只能按路径模板还原。顺序很关键：带 query 的退款进度页要先于订单详情判断。
    """
    if not href:
        return ""
    if "section=refunds" in href:
        return "VIEW_REFUNDS"
    if href.endswith("/cancel"):
        return "CANCEL_BOOKING"
    if href.endswith("/travelers"):
        return "EDIT_TRAVELER"
    if href.endswith("/refund"):
        return "APPLY_REFUND"
    if re.search(r"/me/orders/[^/?#]+$", href):
        return "OPEN_ORDER_DETAIL"
    if href.rstrip("/") == "/me":
        return "BROWSE_ORDERS"
    return ""


def _extract_contexts(tool_calls: list) -> tuple[list[str], list[str]]:
    """抽取本次真正喂给模型的知识片段，作为 RAGAS 的 retrieved_contexts。

    只取 search_knowledge 的成功结果；订单/出行等业务数据不是知识检索，
    强行算 context_precision 没有意义。同时返回知识编号，用于 hit_rate / mrr。
    """
    contexts: list[str] = []
    context_ids: list[str] = []
    for call in tool_calls:
        if call.tool_name != "search_knowledge" or not call.success:
            continue
        payload = call.result or {}
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            title = str(item.get("title") or "").strip()
            contexts.append(f"{title}\n{content}".strip() if title else content)
            context_ids.append(str(item.get("knowledge_id") or "").strip())
    return contexts, context_ids


def _summarize_tool_calls(tool_calls: list) -> list[dict]:
    """把工具调用压成便于写报告的字典。"""
    summary = []
    for call in tool_calls:
        payload = call.result or {}
        summary.append(
            {
                "tool_name": call.tool_name,
                "arguments": call.arguments,
                "success": call.success,
                "message": payload.get("message", ""),
            }
        )
    return summary


def _parse_run(run: AgentRun) -> tuple[str, str, str, str, str]:
    """把 AgentRun.result 拆成 (reply_type, content, action_code, action_href, handoff_reason)。"""
    result = run.result or {}

    if run.state == AgentRunState.HANDED_OFF:
        return (
            "REQUEST_HANDOFF",
            str(result.get("message") or ""),
            "",
            "",
            str(result.get("reason_code") or ""),
        )

    if run.state == AgentRunState.FAILED:
        return ("", str(run.error or result.get("message") or ""), "", "", "")

    content_block = result.get("content")
    if not isinstance(content_block, dict):
        return ("", "", "", "", "")

    action = content_block.get("action") or {}
    href = str(action.get("href") or "") if isinstance(action, dict) else ""
    return (
        str(content_block.get("reply_type") or ""),
        str(content_block.get("text") or ""),
        action_code_from_href(href),
        href,
        "",
    )


async def run_case(
    case: EvalCase,
    agent,
    settings: EvalSettings,
    user_token: str,
) -> CaseRunResult:
    """执行一条样本并返回可评估结果。异常不外抛，转成 error 字段保留样本。"""
    user_id = case.user_id or settings.eval_user_id
    result = CaseRunResult(case_id=case.case_id)

    try:
        # 1. 用独立会话跑完整链路；coordinator 内部负责提交 AgentRun。
        async with session_factory() as session:
            reader = ToolCallReader(ToolCallRepository(session))
            executor = AgentExecutor(
                agent=agent,
                output_validator=AgentOutputValidator(reader),
                max_correction_attempts=settings.agent_correction_limit,
            )
            coordinator = AgentRunCoordinator(session=session, executor=executor)
            event = await coordinator.start_run(
                user_id=user_id,
                access_token=user_token,
                request=_build_request(case),
            )
        result.run_id = event["event_data"]["run_id"]
    except Exception as exc:  # noqa: BLE001 - 评估要把失败样本留下来
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    # 2. 换一个会话读回持久化结果与工具证据，保证读到的是最终状态。
    try:
        async with session_factory() as session:
            run = await AgentRunRepository(session).find_by_id(result.run_id)
            if run is None:
                result.error = "AgentRun 未落库"
                return result
            tool_calls = await ToolCallRepository(session).list_by_run_id(result.run_id)

        result.state = str(run.state)
        result.latency_ms = run.latency_ms
        result.input_tokens = run.input_tokens
        result.output_tokens = run.output_tokens
        (
            result.reply_type,
            result.reply_content,
            result.action_code,
            result.action_href,
            result.handoff_reason,
        ) = _parse_run(run)
        result.contexts, result.context_ids = _extract_contexts(tool_calls)
        result.tool_calls = _summarize_tool_calls(tool_calls)
    except Exception as exc:  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"

    return result
