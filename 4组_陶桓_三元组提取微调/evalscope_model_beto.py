"""SFT/DPO 三元组评估入口。

默认执行不需要外部 API 的 gold 指标评估。LLM Arena 仅作为可选辅助功能，
并从环境变量读取密钥，避免在源码中保存凭据。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from eval_triples import evaluate, load_jsonl, resolve_path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = SCRIPT_DIR / "data" / "eval_questions_with_gold.jsonl"
DEFAULT_SFT = SCRIPT_DIR / "data" / "sft_responses_fixed.jsonl"
DEFAULT_DPO = SCRIPT_DIR / "data" / "dpo_responses_fixed.jsonl"
DEFAULT_METRICS = SCRIPT_DIR / "data" / "triple_eval_results.json"
DEFAULT_ARENA = SCRIPT_DIR / "data" / "arena_results_fixed.json"

JUDGE_SYSTEM_PROMPT = """
你是一名严格、公正的信息抽取质量评估员。你会看到同一问题的回答 A 和回答 B。
请比较实体、关系、完整性、幻觉和 `(头实体, 关系, 尾实体)` 格式。
最终必须严格使用以下五种格式之一：
[[A>>B]]、[[A>B]]、[[A=B]]、[[B>A]]、[[B>>A]]
""".strip()

JUDGE_PROMPT_TEMPLATE = """【用户问题】
{question}

【回答 A】
{answer_a}

【回答 B】
{answer_b}

请简要分析后给出最终结论。
"""


def parse_judge_output(output: str) -> dict:
    match = re.search(r"\[\[\s*([AB])\s*(>>|>|=|<|<<)\s*([AB])\s*\]\]", output)
    if not match:
        return {"winner": "unknown", "score": None, "raw": output}
    left, operator, right = match.groups()
    if left == right:
        return {"winner": "unknown", "score": None, "raw": output}
    if operator == "=":
        return {"winner": "tie", "score": 0, "raw": output}
    left_wins = operator in {">", ">>"}
    winner = left if left_wins else right
    strength = 2 if len(operator) == 2 else 1
    score = strength if winner == "A" else -strength
    return {"winner": winner, "score": score, "raw": output}


def load_aligned_answers(manifest: list[dict], path: Path) -> list[dict]:
    responses = load_jsonl(path)
    # evaluate 的严格对齐检查同时防止 zip 截断和 query/id 错位。
    evaluate(manifest, responses, path, max_errors=0)
    return responses


def judge_single(client, model: str, question: str, answer_a: str, answer_b: str,
                 retry_count: int, retry_delay: float) -> dict:
    user_prompt = JUDGE_PROMPT_TEMPLATE.format(
        question=question,
        answer_a=answer_a,
        answer_b=answer_b,
    )
    for attempt in range(retry_count):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=2048,
                extra_body={"thinking": {"type": "disabled"}},
            )
            return parse_judge_output(response.choices[0].message.content or "")
        except Exception as error:
            if attempt + 1 == retry_count:
                return {"winner": "error", "score": None, "raw": str(error)}
            time.sleep(retry_delay * (attempt + 1))
    return {"winner": "error", "score": None, "raw": "unexpected retry exit"}


def run_objective(args: argparse.Namespace) -> None:
    manifest_path = resolve_path(args.manifest)
    manifest = load_jsonl(manifest_path)
    reports = {}
    for configured in (args.sft_responses, args.dpo_responses):
        path = resolve_path(configured)
        report = evaluate(manifest, load_jsonl(path), path, args.max_errors)
        reports[path.stem] = report
        print(
            f"{path.name}: exact={report['exact_match']:.4f}, "
            f"P/R/F1={report['micro_precision']:.4f}/"
            f"{report['micro_recall']:.4f}/{report['micro_f1']:.4f}, "
            f"format_error={report['format_error_rate']:.4f}"
        )
    output = resolve_path(args.metrics_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as file:
        json.dump({"manifest": str(manifest_path), "models": reports}, file,
                  ensure_ascii=False, indent=2)
    print(f"✅ 客观指标已保存: {output}")


def run_arena(args: argparse.Namespace) -> None:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("运行 --arena 前请设置环境变量 DEEPSEEK_API_KEY")
    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError("运行 --arena 需要安装 openai") from error

    manifest = load_jsonl(resolve_path(args.manifest))
    sft = load_aligned_answers(manifest, resolve_path(args.sft_responses))
    dpo = load_aligned_answers(manifest, resolve_path(args.dpo_responses))
    client = OpenAI(base_url=args.judge_api_url, api_key=api_key)
    tasks = [
        (index, gold["query"], a.get("response", ""), b.get("response", ""))
        for index, (gold, a, b) in enumerate(zip(manifest, sft, dpo))
    ]
    results: list[dict | None] = [None] * len(tasks)
    lock = threading.Lock()
    completed = 0

    def process(task: tuple[int, str, str, str]) -> tuple[int, dict]:
        index, question, answer_a, answer_b = task
        if not answer_a or not answer_b:
            return index, {"winner": "error", "score": None, "raw": "empty answer"}
        return index, judge_single(
            client, args.judge_model, question, answer_a, answer_b,
            args.retry_count, args.retry_delay,
        )

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(process, task) for task in tasks]
        for future in as_completed(futures):
            index, result = future.result()
            results[index] = result
            with lock:
                completed += 1
                if completed % 10 == 0 or completed == len(tasks):
                    print(f"Arena 进度: {completed}/{len(tasks)}")

    stats = {"A_win": 0, "B_win": 0, "tie": 0, "error": 0, "unknown": 0}
    details = []
    for index, (gold, a, b) in enumerate(zip(manifest, sft, dpo)):
        result = results[index] or {"winner": "error", "score": None, "raw": "missing"}
        winner = result["winner"]
        key = {"A": "A_win", "B": "B_win", "tie": "tie",
               "error": "error"}.get(winner, "unknown")
        stats[key] += 1
        details.append({
            "id": gold.get("id"),
            "question": gold["query"],
            "answer_a": a.get("response", ""),
            "answer_b": b.get("response", ""),
            "judge_result": result,
        })

    valid = stats["A_win"] + stats["B_win"] + stats["tie"]
    summary = {
        "total": len(tasks),
        "valid": valid,
        **stats,
        "sft_win_rate": stats["A_win"] / valid if valid else 0.0,
        "dpo_win_rate": stats["B_win"] / valid if valid else 0.0,
        "tie_rate": stats["tie"] / valid if valid else 0.0,
    }
    output = resolve_path(args.arena_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as file:
        json.dump({"summary": summary, "details": details}, file,
                  ensure_ascii=False, indent=2)
    print(f"✅ Arena 结果已保存: {output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--sft-responses", type=Path, default=DEFAULT_SFT)
    parser.add_argument("--dpo-responses", type=Path, default=DEFAULT_DPO)
    parser.add_argument("--metrics-output", type=Path, default=DEFAULT_METRICS)
    parser.add_argument("--max-errors", type=int, default=100)
    parser.add_argument("--arena", action="store_true", help="额外运行外部 LLM Judge")
    parser.add_argument("--arena-output", type=Path, default=DEFAULT_ARENA)
    parser.add_argument("--judge-api-url", default="https://api.deepseek.com")
    parser.add_argument("--judge-model", default="deepseek-v4-flash")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--retry-count", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_objective(args)
    if args.arena:
        run_arena(args)


if __name__ == "__main__":
    main()
