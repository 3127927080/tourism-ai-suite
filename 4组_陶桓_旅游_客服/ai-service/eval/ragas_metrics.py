"""方向一：RAG 调用评估（RAGAS 0.4.x）。

只评估 `search_knowledge` 的检索结果与最终回答：
检索内容对不对、回答是否忠于检索内容、查不到时是否编造。

约束：
1. `retrieved_contexts` 只取 `search_knowledge` 成功结果中的知识正文；
2. 订单/物流等业务数据不参与 RAG 指标；
3. `contexts` 为空的样本不进入检索类指标的分母；
4. 依赖 embedding 的指标在未配置 `EVAL_EMBEDDING_*` 时自动跳过。

RAGAS 是同步 API，内部会自建事件循环，因此统一放到线程里执行，
避免和本项目的 Selector 事件循环互相冲突。
"""

import asyncio
import logging
import math
from dataclasses import dataclass
from typing import Any, Callable

from eval.agent_runner import CaseRunResult
from eval.cases import EvalCase
from eval.config import EvalSettings

logger = logging.getLogger(__name__)

# 需要 reference 的指标必须先有标准答案。
REFERENCE_METRICS = {
    "context_precision",
    "context_recall",
    "answer_correctness",
}

# 依赖 embedding 的指标必须配置 EVAL_EMBEDDING_*。
EMBEDDING_METRICS = {"answer_relevancy", "answer_correctness"}


@dataclass(frozen=True)
class RagasScore:
    """一条样本在一个指标上的结果。"""

    value: float | None = None
    error: str = ""

    @property
    def scored(self) -> bool:
        return self.value is not None


class RagasEvaluator:
    """把 Agent 执行结果转成 RAGAS 样本并逐指标评分。"""

    def __init__(self, settings: EvalSettings):
        self.settings = settings
        self._llm: Any = None
        self._embeddings: Any = None
        self._metric_classes: dict[str, type] | None = None

    @property
    def judge_model(self) -> str:
        """judge 模型留空时复用被测模型。"""
        return self.settings.eval_judge_model or self.settings.llm_model

    def ready(self) -> tuple[bool, str]:
        """在真正调用模型前给出可操作的跳过原因。"""
        try:
            import ragas  # noqa: F401
        except ImportError:
            return False, (
                "未安装 ragas，请执行 "
                "uv pip install -r eval/requirements-eval.txt"
            )

        if not self.judge_model:
            return False, "未配置 llm_model / EVAL_JUDGE_MODEL"

        return True, ""

    def metric_names(self) -> list[str]:
        """返回本次实际会运行的指标名。"""
        names = ["faithfulness", "context_precision", "context_recall"]
        if self.settings.embedding_ready:
            names.extend(["answer_correctness", "answer_relevancy"])
        return names

    def _ensure_loaded(self) -> None:
        """首次评分时再加载 ragas，未安装时不影响规则层运行。"""
        if self._metric_classes is not None:
            return

        try:
            from openai import AsyncOpenAI
            from ragas.llms import llm_factory
            from ragas.metrics.collections import (
                AnswerCorrectness,
                AnswerRelevancy,
                ContextPrecision,
                ContextRecall,
                Faithfulness,
            )
        except ImportError as exc:  # pragma: no cover - 依赖缺失时的提示
            raise RuntimeError(
                "RAGAS 0.4.x 未安装或版本不匹配（ragas 0.4.3 依赖 "
                "langchain-community<0.4，新版社区包会缺少 "
                "langchain_community.chat_models.vertexai），"
                "请执行 uv pip install -r eval/requirements-eval.txt"
            ) from exc

        client = AsyncOpenAI(
            api_key=self.settings.judge_api_key,
            base_url=self.settings.judge_base_url,
            timeout=self.settings.eval_timeout_seconds,
        )
        self._llm = llm_factory(self.judge_model, client=client)

        if self.settings.embedding_ready:
            # 0.4.x 的 embedding_factory 已标记 deprecated，直接实例化 provider 类。
            from ragas.embeddings import OpenAIEmbeddings

            embedding_client = AsyncOpenAI(
                api_key=self.settings.embedding_api_key,
                base_url=self.settings.eval_embedding_base_url,
                timeout=self.settings.eval_timeout_seconds,
            )
            self._embeddings = OpenAIEmbeddings(
                client=embedding_client,
                model=self.settings.eval_embedding_model,
            )

        self._metric_classes = {
            "faithfulness": lambda: Faithfulness(llm=self._llm),
            "context_precision": lambda: ContextPrecision(llm=self._llm),
            "context_recall": lambda: ContextRecall(llm=self._llm),
            "answer_correctness": lambda: AnswerCorrectness(
                llm=self._llm,
                embeddings=self._embeddings,
            ),
            "answer_relevancy": lambda: AnswerRelevancy(
                llm=self._llm,
                embeddings=self._embeddings,
            ),
        }

    async def score(
        self,
        records: list[tuple[EvalCase, CaseRunResult]],
    ) -> dict[str, dict[str, RagasScore]]:
        """返回 case_id -> 指标名 -> 分数。

        每个指标单独跑一批样本，避免「缺 reference」的样本拖垮其它指标，
        也保证样本与分数行号严格一一对应。
        """
        output: dict[str, dict[str, RagasScore]] = {
            case.case_id: {} for case, _ in records
        }

        ready, reason = self.ready()
        if not ready:
            for case, _ in records:
                output[case.case_id]["_skipped"] = RagasScore(error=reason)
            return output

        try:
            self._ensure_loaded()
        except Exception as exc:  # noqa: BLE001 - 版本或配置问题不终止整轮评估
            for case, _ in records:
                output[case.case_id]["_skipped"] = RagasScore(
                    error=f"{type(exc).__name__}: {exc}"
                )
            return output
        assert self._metric_classes is not None

        for name in self.metric_names():
            eligible = [
                (case, run)
                for case, run in records
                if self._eligible(name, case, run)
            ]
            if not eligible:
                continue

            factory: Callable[[], Any] = self._metric_classes[name]
            try:
                scores = await asyncio.to_thread(
                    self._evaluate,
                    factory(),
                    eligible,
                )
            except Exception as exc:  # noqa: BLE001 - 单指标失败不终止整轮评估
                logger.exception("RAGAS 指标 %s 执行失败", name)
                for case, _ in eligible:
                    output[case.case_id][name] = RagasScore(
                        error=f"{type(exc).__name__}: {exc}"
                    )
                continue

            for (case, _), score in zip(eligible, scores):
                output[case.case_id][name] = score

        return output

    def _eligible(self, name: str, case: EvalCase, run: CaseRunResult) -> bool:
        """判断一条样本是否满足指标的计算前提。"""
        if run.failed or not run.reply_content:
            return False
        if name in EMBEDDING_METRICS and not self.settings.embedding_ready:
            return False
        # 检索类指标必须有检索结果，否则没有分母意义。
        if name in {"faithfulness", "context_precision", "context_recall"}:
            if not run.contexts:
                return False
        if name in REFERENCE_METRICS and not case.ground_truth:
            return False
        return True

    def _evaluate(
        self,
        metric: Any,
        eligible: list[tuple[EvalCase, CaseRunResult]],
    ) -> list[RagasScore]:
        """在线程中执行一次 RAGAS 评分，返回与样本同序的分数。"""
        from ragas import evaluate
        from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
        from ragas.run_config import RunConfig

        samples = [
            SingleTurnSample(
                user_input=case.question,
                response=run.reply_content,
                retrieved_contexts=run.contexts,
                reference=case.ground_truth or None,
            )
            for case, run in eligible
        ]
        dataset = EvaluationDataset(samples=samples)

        result = evaluate(
            dataset=dataset,
            metrics=[metric],
            run_config=RunConfig(
                timeout=self.settings.eval_timeout_seconds,
                max_workers=1,
            ),
            raise_exceptions=False,
            show_progress=False,
        )
        frame = result.to_pandas()
        column = _find_metric_column(frame.columns, metric)

        if column is None:
            raise RuntimeError(
                f"RAGAS 结果中找不到指标列，现有列：{list(frame.columns)}"
            )

        scores: list[RagasScore] = []
        for value in frame[column].tolist():
            number = _to_float_or_none(value)
            scores.append(
                RagasScore(value=number)
                if number is not None
                else RagasScore(error="指标未产出分数")
            )
        return scores


def _find_metric_column(columns: Any, metric: Any) -> str | None:
    """兼容 RAGAS 不同小版本的列名写法。"""
    candidates = [
        getattr(metric, "name", None),
        metric.__class__.__name__,
        metric.__class__.__name__.lower(),
    ]
    column_names = list(columns)
    for candidate in candidates:
        if candidate and candidate in column_names:
            return candidate

    # 兜底：排除样本输入列后，若只剩一列就是指标列。
    input_columns = {
        "user_input",
        "retrieved_contexts",
        "response",
        "reference",
        "ground_truth",
    }
    extras = [name for name in column_names if name not in input_columns]
    return extras[0] if len(extras) == 1 else None


def _to_float_or_none(value: Any) -> float | None:
    """把 NaN / None / 非数值统一转换成 None，避免污染均值。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number
