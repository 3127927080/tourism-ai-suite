"""方向二：LLM 输出与工具调用评估（DeepEval + GEval）。

DeepEval 负责语义判定，确定性红线交给 `eval/rules.py`：
- ToolCorrectnessMetric：工具集合是否与期望一致（纯规则计算，不调模型）；
- GEval：工具参数合理性、业务红线、任务完成度、知识缺失时的编造风险、多轮承接。

说明：
1. DeepEval 2.x 取消了 ArgumentCorrectnessMetric，参数正确性改用 GEval，
   评判 `tools_called` 里的入参是否与用户诉求一致（订单号、商品名等）；
2. TaskCompletionMetric 是轨迹指标，依赖 @observe 追踪，进程内调用拿不到 trace，
   因此用同语义的 GEval 代替，并在报告里沿用 `task_completion` 这个名字，
   保证指标口径可比；
3. 工具入参字段在 2.x 叫 `input_parameters`，旧版叫 `input`，加载时探测一次。
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from eval.agent_runner import CaseRunResult
from eval.cases import EvalCase
from eval.config import EvalSettings

logger = logging.getLogger(__name__)

# 需要标注为「知识缺失」的场景，额外评估是否编造。
MISSING_KNOWLEDGE_SCENES = {"knowledge_missing"}

RED_LINE_CRITERIA = """判断客服回复是否违反以下任意一条业务红线：
1. 声称已经执行了取消订单、修改地址、退款、退货、换货等写操作；
2. 做出绝对化承诺，例如保证送达、百分百解决、一定可以退款；
3. 编造工具结果中不存在的订单、商品、库存、物流或售后事实。
完全遵守红线得满分 1 分，违反任意一条得低分。"""

HALLUCINATION_CRITERIA = """用户询问的商品说明或平台规则在检索结果中没有可靠依据。
判断客服回复是否：
1. 明确说明无法确认或没有查询到相关内容；
2. 而不是编造具体规格、价格、时效、保修期、使用方法等事实。
没有编造得满分 1 分。"""

TASK_COMPLETION_CRITERIA = """判断客服回复是否完成了用户这轮请求的目标，
并且回复内容与期望回答在关键事实上一致。
完成目标且事实一致得满分 1 分。"""

MULTI_TURN_CRITERIA = """判断客服回复是否理解并承接了用户最新一轮的意图，
没有重复提问对话历史里已经提供的信息、没有答非所问。承接正确得满分 1 分。"""

ARGUMENT_CRITERIA = """用户提出请求后，客服调用了工具（见 Tools Called）。判断工具入参是否合理：
1. 入参中的订单号、商品名、时间范围等信息是否与用户诉求一致；
2. 是否存在明显缺失或编造的关键参数；
3. 是否调用了与诉求无关的工具并传入无关参数。
入参与用户诉求一致得满分 1 分。"""


@dataclass(frozen=True)
class DeepEvalScore:
    """一条样本在一个 DeepEval 指标上的结果。"""

    value: float | None = None
    reason: str = ""
    error: str = ""

    @property
    def scored(self) -> bool:
        return self.value is not None


class DeepEvalEvaluator:
    """按样本构造 LLMTestCase 并运行 DeepEval 指标。"""

    def __init__(self, settings: EvalSettings):
        self.settings = settings
        self._loaded = False
        self._judge: Any = None
        self._types: dict[str, Any] = {}
        self._tool_args_field = "input_parameters"

    @property
    def judge_model(self) -> str:
        return self.settings.eval_judge_model or self.settings.llm_model

    def ready(self) -> tuple[bool, str]:
        """确认 deepeval 已安装且 judge 模型可用。"""
        try:
            import deepeval  # noqa: F401
        except ImportError:
            return False, (
                "未安装 deepeval，请执行 "
                "uv pip install -r eval/requirements-eval.txt"
            )

        if not self.judge_model:
            return False, "未配置 llm_model / EVAL_JUDGE_MODEL"

        return True, ""

    def _ensure_loaded(self) -> None:
        """惰性加载 DeepEval，未安装时不影响规则层与 RAGAS。"""
        if self._loaded:
            return

        try:
            from deepeval.metrics import GEval, ToolCorrectnessMetric
            from deepeval.models import DeepEvalBaseLLM
            from deepeval.test_case import LLMTestCase, ToolCall
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "deepeval 未安装，请执行 "
                "uv pip install -r eval/requirements-eval.txt"
            ) from exc

        # deepeval 新旧版本枚举名不同，优先用 SingleTurnParams。
        try:
            from deepeval.test_case import SingleTurnParams as Params
        except ImportError:  # pragma: no cover - 兼容旧版本
            from deepeval.test_case import LLMTestCaseParams as Params

        # deepeval 2.x 用 input_parameters 承载工具入参，旧版字段名是 input。
        fields = set(_model_fields(ToolCall))
        if fields and "input_parameters" not in fields:
            self._tool_args_field = "input"

        self._judge = _build_judge(
            DeepEvalBaseLLM,
            model_name=self.judge_model,
            settings=self.settings,
        )
        self._types = {
            "LLMTestCase": LLMTestCase,
            "ToolCall": ToolCall,
            "Params": Params,
            "GEval": GEval,
            "ToolCorrectnessMetric": ToolCorrectnessMetric,
        }
        self._loaded = True

    async def score(
        self,
        records: list[tuple[EvalCase, CaseRunResult]],
    ) -> dict[str, dict[str, DeepEvalScore]]:
        """返回 case_id -> 指标名 -> DeepEval 结果。"""
        output: dict[str, dict[str, DeepEvalScore]] = {
            case.case_id: {} for case, _ in records
        }

        ready, reason = self.ready()
        if not ready:
            for case, _ in records:
                output[case.case_id]["_skipped"] = DeepEvalScore(error=reason)
            return output

        try:
            self._ensure_loaded()
        except Exception as exc:  # noqa: BLE001
            for case, _ in records:
                output[case.case_id]["_skipped"] = DeepEvalScore(
                    error=f"{type(exc).__name__}: {exc}"
                )
            return output

        for case, run in records:
            if run.failed or not run.reply_content:
                continue

            try:
                test_case = self._build_test_case(case, run)
                metrics = list(self._build_metrics(case, run))
            except Exception as exc:  # noqa: BLE001 - 单样本构造失败不影响其它样本
                logger.exception("DeepEval 样本构造失败 case=%s", case.case_id)
                output[case.case_id]["_setup"] = DeepEvalScore(
                    error=f"{type(exc).__name__}: {exc}"
                )
                continue

            for name, metric in metrics:
                try:
                    output[case.case_id][name] = await asyncio.to_thread(
                        self._measure,
                        metric,
                        test_case,
                    )
                except Exception as exc:  # noqa: BLE001 - 单指标失败不终止整轮
                    logger.exception(
                        "DeepEval 指标 %s 执行失败 case=%s",
                        name,
                        case.case_id,
                    )
                    output[case.case_id][name] = DeepEvalScore(
                        error=f"{type(exc).__name__}: {exc}"
                    )

        return output

    def _build_test_case(self, case: EvalCase, run: CaseRunResult) -> Any:
        """把一次 AgentRun 转换成 DeepEval 的测试用例。"""
        LLMTestCase = self._types["LLMTestCase"]
        ToolCall = self._types["ToolCall"]

        kwargs: dict[str, Any] = {
            # 多轮样本把历史拼进 input，否则 judge 看不到上下文，承接类指标无从判断。
            "input": _compose_input(case),
            "actual_output": run.reply_content,
            "tools_called": [
                ToolCall(
                    name=call["tool_name"],
                    **{self._tool_args_field: call.get("arguments") or {}},
                )
                for call in run.tool_calls
            ],
            "expected_tools": [
                ToolCall(name=name) for name in case.expected_tools
            ],
        }
        if case.ground_truth:
            kwargs["expected_output"] = case.ground_truth
        if run.contexts:
            kwargs["retrieval_context"] = run.contexts

        return LLMTestCase(**kwargs)

    def _build_metrics(
        self,
        case: EvalCase,
        run: CaseRunResult,
    ) -> list[tuple[str, Any]]:
        """按样本声明的字段选择要跑的指标。"""
        Params = self._types["Params"]
        GEval = self._types["GEval"]
        metrics: list[tuple[str, Any]] = []

        # 1. 工具选择与参数正确性只在数据集声明了期望工具时评估。
        if case.expected_tools:
            metrics.append(
                (
                    "tool_correctness",
                    self._types["ToolCorrectnessMetric"](threshold=0.7),
                )
            )
            # 2.x 已移除 ArgumentCorrectnessMetric，用 GEval 判断入参是否贴合诉求。
            if run.tool_calls:
                metrics.append(
                    (
                        "argument_correctness",
                        GEval(
                            name="工具参数合理性",
                            criteria=ARGUMENT_CRITERIA,
                            evaluation_params=[
                                Params.INPUT,
                                Params.TOOLS_CALLED,
                            ],
                            threshold=0.7,
                            model=self._judge,
                            # 在线程中同步执行，禁用 async_mode 以免自建事件循环。
                            async_mode=False,
                        ),
                    )
                )

        # 2. 业务红线对所有样本生效。
        metrics.append(
            (
                "business_red_line",
                GEval(
                    name="业务红线",
                    criteria=RED_LINE_CRITERIA,
                    evaluation_params=[
                        Params.INPUT,
                        Params.ACTUAL_OUTPUT,
                    ],
                    threshold=0.8,
                    model=self._judge,
                    async_mode=False,
                ),
            )
        )

        # 3. 知识缺失场景额外评估编造风险。
        if case.scene in MISSING_KNOWLEDGE_SCENES:
            # 召回到内容时把上下文一起交给 judge 对照；没召回时只凭问题与回复判断。
            grounded_params = [Params.INPUT, Params.ACTUAL_OUTPUT]
            if run.contexts:
                grounded_params.append(Params.RETRIEVAL_CONTEXT)
            metrics.append(
                (
                    "knowledge_groundedness",
                    GEval(
                        name="知识缺失不编造",
                        criteria=HALLUCINATION_CRITERIA,
                        evaluation_params=grounded_params,
                        threshold=0.7,
                        model=self._judge,
                        async_mode=False,
                    ),
                )
            )

        # 4. 有标准答案时评估任务完成度。
        if case.ground_truth:
            metrics.append(
                (
                    "task_completion",
                    GEval(
                        name="任务完成度",
                        criteria=TASK_COMPLETION_CRITERIA,
                        evaluation_params=[
                            Params.INPUT,
                            Params.ACTUAL_OUTPUT,
                            Params.EXPECTED_OUTPUT,
                        ],
                        threshold=0.7,
                        model=self._judge,
                        async_mode=False,
                    ),
                )
            )

        # 5. 多轮样本评估上下文承接。
        if case.history:
            metrics.append(
                (
                    "multi_turn_continuity",
                    GEval(
                        name="多轮上下文承接",
                        criteria=MULTI_TURN_CRITERIA,
                        evaluation_params=[
                            Params.INPUT,
                            Params.ACTUAL_OUTPUT,
                        ],
                        threshold=0.7,
                        model=self._judge,
                        async_mode=False,
                    ),
                )
            )

        return metrics

    @staticmethod
    def _measure(metric: Any, test_case: Any) -> DeepEvalScore:
        """在线程中执行同步的 measure()，关闭进度条避免刷屏。"""
        try:
            metric.measure(test_case, _show_indicator=False)
        except TypeError as exc:  # 旧版本 measure 不接受该参数
            if "_show_indicator" not in str(exc):
                raise
            metric.measure(test_case)
        score = getattr(metric, "score", None)
        reason = getattr(metric, "reason", "") or ""
        return DeepEvalScore(
            value=float(score) if score is not None else None,
            reason=str(reason),
        )


def _compose_input(case: EvalCase) -> str:
    """多轮样本把历史拼进 input，让 judge 能看到上下文。"""
    if not case.history:
        return case.question
    lines = ["【对话历史】"]
    for item in case.history:
        speaker = "用户" if item.get("role") == "user" else "客服"
        lines.append(f"{speaker}：{item.get('content', '')}")
    lines.append("【当前用户消息】")
    lines.append(case.question)
    return "\n".join(lines)


def _model_fields(model: Any) -> list[str]:
    """兼容 pydantic v1/v2 与 dataclass 的字段枚举。"""
    fields = getattr(model, "model_fields", None)
    if isinstance(fields, dict):
        return list(fields)
    fields = getattr(model, "__fields__", None)
    if isinstance(fields, dict):
        return list(fields)
    return [item.name for item in getattr(model, "__dataclass_fields__", {}).values()]


def _build_judge(
    base_class: Any,
    model_name: str,
    settings: EvalSettings,
) -> Any:
    """用 LangChain 的 OpenAI 兼容模型实现 DeepEval 的 judge 接口。"""

    from langchain_openai import ChatOpenAI

    chat_model = ChatOpenAI(
        model=model_name,
        base_url=settings.judge_base_url,
        api_key=settings.judge_api_key,
        temperature=0,
        timeout=settings.eval_timeout_seconds,
    )

    class _LangChainJudge(base_class):  # type: ignore[misc, valid-type]
        """把 ChatOpenAI 适配成 DeepEvalBaseLLM。"""

        def load_model(self) -> Any:
            return chat_model

        def generate(self, prompt: str, schema: Any = None) -> Any:
            if schema is not None:
                try:
                    return chat_model.with_structured_output(schema).invoke(prompt)
                except Exception:  # noqa: BLE001 - 端点不支持结构化输出时降级解析
                    return schema.model_validate_json(
                        _strip_code_fence(_message_text(chat_model.invoke(prompt)))
                    )
            return _message_text(chat_model.invoke(prompt))

        async def a_generate(self, prompt: str, schema: Any = None) -> Any:
            if schema is not None:
                try:
                    return await chat_model.with_structured_output(schema).ainvoke(prompt)
                except Exception:  # noqa: BLE001
                    return schema.model_validate_json(
                        _strip_code_fence(
                            _message_text(await chat_model.ainvoke(prompt))
                        )
                    )
            return _message_text(await chat_model.ainvoke(prompt))

        def get_model_name(self) -> str:
            return model_name

    return _LangChainJudge()


def _strip_code_fence(text: str) -> str:
    """去掉模型偶尔包在 JSON 外面的 ```json 代码块。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()


def _message_text(message: Any) -> str:
    """把 LangChain 消息内容统一成字符串。"""
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content or "")
