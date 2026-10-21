"""评估 CLI 主入口。

用法（项目根目录）：

    # 冒烟：只跑一条样本，跳过两个语义层
    uv run python -m eval.run_eval --limit 1 --skip-ragas --skip-deepeval

    # 只跑知识类样本
    uv run python -m eval.run_eval --scene policy

    # 全量
    uv run python -m eval.run_eval

评估会在 `agent_runs` / `agent_tool_calls` 写入记录，conversation_id 统一带
`eval-conv-` 前缀，便于清理。
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from atguigu.agent.factory import create_support_agent
from atguigu.common.event_loop import run_async
from eval.agent_runner import CaseRunResult, build_eval_token, run_case
from eval.cases import EvalCase, default_dataset_paths, load_cases
from eval.config import EvalSettings, get_eval_settings
from eval.deepeval_metrics import DeepEvalEvaluator
from eval.ragas_metrics import RagasEvaluator
from eval.report import CaseRecord, build_summary, print_summary, write_reports
from eval.rules import evaluate_rules


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Service 离线评估",
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        type=Path,
        default=None,
        help="CSV 数据集路径，默认加载 eval/datasets 下全部文件",
    )
    parser.add_argument(
        "--scene",
        action="append",
        default=None,
        help="只跑指定场景，可重复",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=None,
        help="只跑指定样本编号，可重复",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="最多跑多少条样本（过滤后生效）",
    )
    parser.add_argument(
        "--skip-ragas",
        action="store_true",
        help="跳过方向一的 RAGAS 语义评分",
    )
    parser.add_argument(
        "--skip-deepeval",
        action="store_true",
        help="跳过方向二的 DeepEval 语义评分",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="报告输出目录，默认 eval/results/<时间戳>",
    )
    return parser.parse_args(argv)


def _select_cases(
    cases: list[EvalCase],
    args: argparse.Namespace,
) -> list[EvalCase]:
    """按场景 / 样本编号过滤，再应用 limit。"""
    selected = cases

    if args.scene:
        scenes = set(args.scene)
        selected = [case for case in selected if case.scene in scenes]

    if args.case_id:
        case_ids = set(args.case_id)
        selected = [case for case in selected if case.case_id in case_ids]

    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("--limit 必须大于 0")
        selected = selected[: args.limit]

    if not selected:
        raise ValueError("过滤后没有可执行样本")

    return selected


def _default_output_dir() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return Path(__file__).resolve().parent / "results" / stamp


async def _execute(
    cases: list[EvalCase],
    settings: EvalSettings,
    user_token: str,
    agent,
) -> list[CaseRecord]:
    """顺序执行样本：先规则层，再决定是否进入语义层。"""
    records: list[CaseRecord] = []
    total = len(cases)

    for index, case in enumerate(cases, start=1):
        run: CaseRunResult = await run_case(case, agent, settings, user_token)
        rules = evaluate_rules(case, run)
        status = "PASS" if rules["rule_passed"] else "FAIL"
        print(
            f"[{index}/{total}] {case.case_id:<16} "
            f"state={run.state or '-':<18} "
            f"reply={run.reply_type or '-':<16} rule={status}"
        )
        if not rules["rule_passed"]:
            print(f"          {rules['detail']}")
        records.append(CaseRecord(case=case, run=run, rules=rules))

    return records


async def _run_semantic_layers(
    records: list[CaseRecord],
    settings: EvalSettings,
    args: argparse.Namespace,
) -> None:
    """语义评分只对规则通过且执行成功的样本进行。"""
    scorable = [
        (record.case, record.run)
        for record in records
        if record.scored
    ]
    if not scorable:
        print("\n规则层没有通过样本，跳过语义评分。")
        return

    if args.skip_ragas:
        print("\n已按参数跳过 RAGAS。")
    else:
        evaluator = RagasEvaluator(settings)
        ready, reason = evaluator.ready()
        if not ready:
            print(f"\nRAGAS 跳过：{reason}")
        else:
            print(f"\nRAGAS 评分中（{len(scorable)} 条）……")
            scores = await evaluator.score(scorable)
            for record in records:
                record.ragas = scores.get(record.case.case_id, {})

    if args.skip_deepeval:
        print("已按参数跳过 DeepEval。")
    else:
        evaluator = DeepEvalEvaluator(settings)
        ready, reason = evaluator.ready()
        if not ready:
            print(f"DeepEval 跳过：{reason}")
        else:
            print(f"DeepEval 评分中（{len(scorable)} 条）……")
            scores = await evaluator.score(scorable)
            for record in records:
                record.deepeval = scores.get(record.case.case_id, {})


async def _main(args: argparse.Namespace) -> int:
    settings = get_eval_settings()

    if not settings.llm_model:
        print("未配置 LLM_MODEL，无法执行评估。", file=sys.stderr)
        return 2

    paths = args.datasets or default_dataset_paths()
    cases = _select_cases(load_cases(paths), args)
    user_token = settings.eval_user_token.strip() or build_eval_token(
        settings,
        settings.eval_user_id,
    )

    print(f"模型：{settings.llm_model}  judge：{settings.eval_judge_model or settings.llm_model}")
    print(f"数据集：{', '.join(path.name for path in paths)}")
    print(f"样本数：{len(cases)}")
    print("-" * 64)

    agent = create_support_agent()
    records = await _execute(cases, settings, user_token, agent)
    await _run_semantic_layers(records, settings, args)

    output_dir = args.output_dir or _default_output_dir()
    meta = {
        "model": settings.llm_model,
        "judge_model": settings.eval_judge_model or settings.llm_model,
        "prompt_version": settings.prompt_version,
        "embedding_ready": settings.embedding_ready,
        "user_token_source": "EVAL_USER_TOKEN" if settings.eval_user_token else "signed",
        "datasets": [str(path) for path in paths],
    }
    summary = build_summary(records, meta)
    csv_path, summary_path = write_reports(records, summary, output_dir)

    print_summary(summary)
    print(f"明细：{csv_path}")
    print(f"汇总：{summary_path}")

    return 0 if summary["rule_pass_count"] == summary["total_cases"] else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return run_async(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
