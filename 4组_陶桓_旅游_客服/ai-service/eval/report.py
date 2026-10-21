"""评估结果汇总。

产物：
- `result.csv`：一行一条样本，规则结果、检索分数、语义分数都在同一行；
- `summary.json`：均值、有效样本数、失败清单，便于版本 diff 和 CI 门禁。

聚合口径：
- 语义指标只统计真正产出分数的样本，NaN / 异常不进入均值分母；
- 检索类指标只统计 `contexts` 非空的样本；
- 规则层失败的样本保留在 CSV 中，但不参与语义指标。
"""

import csv
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eval.agent_runner import CaseRunResult
from eval.cases import EvalCase
from eval.deepeval_metrics import DeepEvalScore
from eval.ragas_metrics import RagasScore

RAGAS_COLUMNS = (
    "faithfulness",
    "context_precision",
    "context_recall",
    "answer_correctness",
    "answer_relevancy",
    "hit_rate",
    "mrr",
)

DEEPEVAL_COLUMNS = (
    "tool_correctness",
    "argument_correctness",
    "business_red_line",
    "knowledge_groundedness",
    "task_completion",
    "multi_turn_continuity",
)


@dataclass
class CaseRecord:
    """一条样本的完整评估记录。"""

    case: EvalCase
    run: CaseRunResult
    rules: dict[str, Any]
    ragas: dict[str, RagasScore] = field(default_factory=dict)
    deepeval: dict[str, DeepEvalScore] = field(default_factory=dict)

    @property
    def scored(self) -> bool:
        """规则通过且执行成功，才允许进入语义评分。"""
        return bool(self.rules.get("rule_passed")) and not self.run.failed


def build_row(record: CaseRecord) -> dict[str, Any]:
    """把一条样本压平成 CSV 行。"""
    case = record.case
    run = record.run

    row: dict[str, Any] = {
        "case_id": case.case_id,
        "scene": case.scene,
        "question": case.question,
        "state": run.state,
        "reply_type": run.reply_type,
        "expected_reply_type": case.expected_reply_type,
        "action_code": run.action_code,
        "expected_action_code": case.expected_action_code,
        "rule_passed": record.rules.get("rule_passed"),
        "rule_detail": record.rules.get("detail", ""),
        "tool_names": "|".join(run.tool_names),
        "missing_tools": "|".join(record.rules.get("missing_tools", [])),
        "extra_tools": "|".join(record.rules.get("extra_tools", [])),
        "contexts_count": len(run.contexts),
        "latency_ms": run.latency_ms,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "error": run.error,
    }

    for name in RAGAS_COLUMNS:
        row[f"ragas_{name}"] = _ragas_value(record, name)

    for name in DEEPEVAL_COLUMNS:
        row[f"deepeval_{name}"] = _deepeval_value(record, name)

    return row


def build_summary(
    records: list[CaseRecord],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """聚合规则结果、检索指标与语义指标。"""
    total = len(records)
    rule_passed = sum(1 for record in records if record.rules.get("rule_passed"))

    summary: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "meta": meta,
        "total_cases": total,
        "rule_pass_count": rule_passed,
        "rule_pass_rate": _ratio(rule_passed, total),
        "rule_failures": [
            {
                "case_id": record.case.case_id,
                "scene": record.case.scene,
                "detail": record.rules.get("detail", ""),
            }
            for record in records
            if not record.rules.get("rule_passed")
        ],
        "by_scene": _by_scene(records),
        "retrieval": _retrieval_metrics(records),
        "answer_type": _answer_type_metrics(records),
        "handoff": _handoff_metrics(records),
        "tool_usage": _tool_usage_metrics(records),
        "ragas": _semantic_stats(records, "ragas"),
        "deepeval": _semantic_stats(records, "deepeval"),
    }
    return summary


def write_reports(
    records: list[CaseRecord],
    summary: dict[str, Any],
    output_dir: Path,
) -> tuple[Path, Path]:
    """写出 result.csv 与 summary.json。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "result.csv"
    summary_path = output_dir / "summary.json"

    rows = [build_row(record) for record in records]
    fieldnames = list(rows[0].keys()) if rows else ["case_id"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with summary_path.open("w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)

    return csv_path, summary_path


def print_summary(summary: dict[str, Any]) -> None:
    """控制台概览，方便冒烟时快速判断。"""
    print()
    print("=" * 64)
    print(f"样本数：{summary['total_cases']}  "
          f"规则通过：{summary['rule_pass_count']}  "
          f"通过率：{summary['rule_pass_rate']}")
    print("-" * 64)

    for name, stats in summary["ragas"].items():
        if stats["mean"] is None:
            continue
        print(f"RAGAS    {name:<22} mean={stats['mean']}  valid={stats['valid']}")
    for name, stats in summary["deepeval"].items():
        if stats["mean"] is None:
            continue
        print(f"DeepEval {name:<22} mean={stats['mean']}  valid={stats['valid']}")

    handoff = summary["handoff"]
    print(
        f"转人工 precision={handoff['precision']} recall={handoff['recall']} "
        f"(tp={handoff['tp']}, fp={handoff['fp']}, fn={handoff['fn']})"
    )
    if summary["rule_failures"]:
        print("-" * 64)
        print("规则层失败：")
        for item in summary["rule_failures"]:
            print(f"  - {item['case_id']} [{item['scene']}] {item['detail']}")
    print("=" * 64)


# ---------------------------------------------------------------------------
# 单指标取值
# ---------------------------------------------------------------------------


def _ragas_value(record: CaseRecord, name: str) -> float | None:
    """检索类指标由本地计算，其余来自 RAGAS。"""
    if name == "hit_rate":
        return _hit_rate(record)
    if name == "mrr":
        return _mrr(record)
    score = record.ragas.get(name)
    return score.value if score is not None else None


def _deepeval_value(record: CaseRecord, name: str) -> float | None:
    score = record.deepeval.get(name)
    return score.value if score is not None else None


def _hit_rate(record: CaseRecord) -> float | None:
    """gold 知识编号是否出现在本次召回中。"""
    gold = set(record.case.gold_knowledge_ids)
    if not gold or not record.scored:
        return None
    return 1.0 if gold & set(record.run.context_ids) else 0.0


def _mrr(record: CaseRecord) -> float | None:
    """第一个命中 gold 知识编号的倒数排名。"""
    gold = set(record.case.gold_knowledge_ids)
    if not gold or not record.scored:
        return None
    for rank, knowledge_id in enumerate(record.run.context_ids, start=1):
        if knowledge_id in gold:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------


def _semantic_stats(records: list[CaseRecord], kind: str) -> dict[str, dict[str, Any]]:
    """按指标汇总语义分数，NaN 与异常不计入分母。"""
    if kind == "ragas":
        names = RAGAS_COLUMNS
        getter = lambda record, name: _ragas_value(record, name)  # noqa: E731
    else:
        names = DEEPEVAL_COLUMNS
        getter = lambda record, name: _deepeval_value(record, name)  # noqa: E731

    result: dict[str, dict[str, Any]] = {}
    for name in names:
        values = [
            value
            for record in records
            if record.scored
            and (value := getter(record, name)) is not None
        ]
        attempted = sum(1 for record in records if record.scored)
        if not values and not attempted:
            continue
        result[name] = {
            "mean": _mean(values),
            "valid": len(values),
            "attempted": attempted,
            "skipped": attempted - len(values),
        }
    return result


def _by_scene(records: list[CaseRecord]) -> dict[str, dict[str, Any]]:
    """按场景统计规则通过率，避免整体均值掩盖某一类退化。"""
    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        scene = record.case.scene or "unknown"
        bucket = grouped.setdefault(scene, {"total": 0, "passed": 0})
        bucket["total"] += 1
        if record.rules.get("rule_passed"):
            bucket["passed"] += 1

    for bucket in grouped.values():
        bucket["pass_rate"] = _ratio(bucket["passed"], bucket["total"])
    return grouped


def _answer_type_metrics(records: list[CaseRecord]) -> dict[str, Any]:
    """四类回复分类准确率与页面动作准确率。"""
    reply_cases = [
        record
        for record in records
        if record.case.expected_reply_type
    ]
    reply_hits = sum(
        1
        for record in reply_cases
        if record.run.reply_type == record.case.expected_reply_type
    )

    action_cases = [
        record
        for record in records
        if record.case.expected_action_code
    ]
    action_hits = sum(
        1
        for record in action_cases
        if record.run.action_code == record.case.expected_action_code
    )

    return {
        "reply_type_accuracy": _ratio(reply_hits, len(reply_cases)),
        "reply_type_total": len(reply_cases),
        "page_action_accuracy": _ratio(action_hits, len(action_cases)),
        "page_action_total": len(action_cases),
    }


def _handoff_metrics(records: list[CaseRecord]) -> dict[str, Any]:
    """转人工的精确率与召回率。"""
    tp = fp = fn = 0
    for record in records:
        expected = record.case.expected_reply_type == "REQUEST_HANDOFF"
        actual = record.run.reply_type == "REQUEST_HANDOFF"
        if expected and actual:
            tp += 1
        elif actual:
            fp += 1
        elif expected:
            fn += 1

    return {
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def _tool_usage_metrics(records: list[CaseRecord]) -> dict[str, Any]:
    """多余工具调用比例：调用了期望集合之外的工具即为一次 over-call。"""
    cases = [record for record in records if record.case.expected_tools]
    over_calls = sum(
        1 for record in cases if record.rules.get("extra_tools")
    )
    return {
        "tool_over_call_rate": _ratio(over_calls, len(cases)),
        "total": len(cases),
        "over_called": over_calls,
    }


def _retrieval_metrics(records: list[CaseRecord]) -> dict[str, Any]:
    """本地检索指标：hit_rate 与 mrr。"""
    values = [
        value
        for record in records
        if record.case.gold_knowledge_ids
        and (value := _hit_rate(record)) is not None
    ]
    mrrs = [
        value
        for record in records
        if record.case.gold_knowledge_ids
        and (value := _mrr(record)) is not None
    ]
    return {
        "hit_rate": _mean(values),
        "hit_rate_valid": len(values),
        "mrr": _mean(mrrs),
        "mrr_valid": len(mrrs),
    }


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)
