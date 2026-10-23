"""用 gold 三元组对模型输出做确定性的本地评估。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = SCRIPT_DIR / "data" / "eval_questions_with_gold.jsonl"
DEFAULT_SFT_RESPONSES = SCRIPT_DIR / "data" / "sft_responses_fixed.jsonl"
DEFAULT_DPO_RESPONSES = SCRIPT_DIR / "data" / "dpo_responses_fixed.jsonl"
DEFAULT_OUTPUT = SCRIPT_DIR / "data" / "triple_eval_results.json"
Triple = tuple[str, str, str]


@dataclass
class ParsedTriples:
    triples: set[Triple]
    format_valid: bool
    extra_text: str
    duplicate_count: int = 0


def resolve_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return SCRIPT_DIR / candidate


def normalize_field(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    # 中文实体内部的空格不应参与比较（"6 天以上" 与 "6天以上" 视为同一实体）
    return re.sub(r"\s+", "", value).strip(",，")


def strip_wrappers(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"```(?:\w+)?", "", text)
    return text.replace("<|im_end|>", "").replace("<|im_start|>", "").strip()


def _parenthesized_segments(text: str) -> tuple[list[str], str, bool]:
    segments: list[str] = []
    outside: list[str] = []
    depth = 0
    inner_depth = 0
    start = -1
    malformed = False
    for index, char in enumerate(text):
        if char == "(":
            if depth == 0:
                start = index + 1
                depth = 1
            elif text[start:index].count(",") >= 2:
                # 尾实体可能包含括注；两次字段分隔符之后的括号属于实体正文。
                inner_depth += 1
            else:
                depth += 1
        elif char == ")":
            if depth == 0:
                malformed = True
                outside.append(char)
            elif inner_depth:
                inner_depth -= 1
            elif depth > 1:
                depth -= 1
            else:
                depth = 0
                segments.append(text[start:index])
                outside.append(" ")
        elif depth == 0:
            outside.append(char)
    if depth:
        # 有些标注的尾实体含中文括号，但外层三元组只以最后一个 ')' 闭合。
        # 只要可恢复出三个非空字段，就交给字段解析判断内容。
        malformed = False
    return segments, "".join(outside).strip(), malformed


def parse_triples(text: str) -> ParsedTriples:
    """解析一行或多行三元组；关系/实体内部可以包含普通标点。"""
    cleaned = strip_wrappers(text)
    if cleaned in {"", "无三元组", "无"}:
        return ParsedTriples(set(), cleaned in {"无三元组", "无"}, "")

    segments, outside, malformed = _parenthesized_segments(cleaned)
    if not segments and cleaned.startswith("("):
        segments = [cleaned[1:].rstrip(")）")]
    triples: set[Triple] = set()
    valid_occurrences = 0
    for segment in segments:
        # 数据格式没有转义机制，因此按前两个逗号切分，尾实体保留后续逗号。
        parts = re.split(r"\s*[,，]\s*", segment, maxsplit=2)
        normalized = tuple(normalize_field(part) for part in parts)
        if len(normalized) != 3 or any(not part for part in normalized):
            malformed = True
            continue
        valid_occurrences += 1
        triples.add(normalized)  # type: ignore[arg-type]

    extra = re.sub(r"[\s;；。]+", "", outside)
    format_valid = bool(triples) and not malformed and not extra
    return ParsedTriples(triples, format_valid, outside, valid_occurrences - len(triples))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def record_key(record: dict[str, Any]) -> tuple[str, str]:
    return str(record.get("id", "")), str(record.get("query", ""))


def align_records(
    manifest: list[dict[str, Any]],
    responses: list[dict[str, Any]],
    response_path: Path,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if len(manifest) != len(responses):
        raise ValueError(
            f"{response_path} 有 {len(responses)} 条，但 manifest 有 {len(manifest)} 条；拒绝截断评估"
        )
    aligned = []
    for index, (gold, prediction) in enumerate(zip(manifest, responses)):
        if gold.get("query") != prediction.get("query"):
            raise ValueError(f"{response_path} 第 {index + 1} 条 query 与 manifest 不一致")
        if "id" in prediction and str(gold.get("id")) != str(prediction.get("id")):
            raise ValueError(f"{response_path} 第 {index + 1} 条 id 与 manifest 不一致")
        aligned.append((gold, prediction))
    return aligned


def evaluate(
    manifest: list[dict[str, Any]],
    responses: list[dict[str, Any]],
    response_path: Path,
    max_errors: int,
) -> dict[str, Any]:
    tp = fp = fn = exact = format_errors = empty_predictions = duplicate_predictions = 0
    errors = []
    aligned = align_records(manifest, responses, response_path)

    for index, (gold_record, prediction_record) in enumerate(aligned):
        gold = parse_triples(str(gold_record.get("gold", "")))
        prediction = parse_triples(str(prediction_record.get("response", "")))
        if not gold.format_valid and str(gold_record.get("gold", "")).strip() not in {"无三元组", "无"}:
            raise ValueError(f"manifest 第 {index + 1} 条 gold 格式无法解析")

        true_positive = len(gold.triples & prediction.triples)
        false_positive = len(prediction.triples - gold.triples)
        false_negative = len(gold.triples - prediction.triples)
        tp += true_positive
        fp += false_positive
        fn += false_negative
        is_exact = prediction.triples == gold.triples and prediction.format_valid
        exact += int(is_exact)
        format_errors += int(not prediction.format_valid)
        empty_predictions += int(not prediction.triples)
        duplicate_predictions += prediction.duplicate_count

        if not is_exact and len(errors) < max_errors:
            errors.append({
                "id": gold_record.get("id"),
                "query": gold_record.get("query"),
                "gold": gold_record.get("gold"),
                "prediction": prediction_record.get("response"),
                "parsed_gold": [list(item) for item in sorted(gold.triples)],
                "parsed_prediction": [list(item) for item in sorted(prediction.triples)],
                "format_valid": prediction.format_valid,
                "extra_text": prediction.extra_text,
            })

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    total = len(aligned)
    return {
        "response_file": str(response_path),
        "total_samples": total,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "micro_precision": precision,
        "micro_recall": recall,
        "micro_f1": f1,
        "exact_match": exact / total if total else 0.0,
        "exact_match_count": exact,
        "format_error_rate": format_errors / total if total else 0.0,
        "format_error_count": format_errors,
        "empty_prediction_rate": empty_predictions / total if total else 0.0,
        "empty_prediction_count": empty_predictions,
        "duplicate_prediction_count": duplicate_predictions,
        "errors": errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--responses", type=Path, nargs="+",
                        default=[DEFAULT_SFT_RESPONSES, DEFAULT_DPO_RESPONSES])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-errors", type=int, default=100)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest_path = resolve_path(args.manifest)
    manifest = load_jsonl(manifest_path)
    reports = {}
    for configured_path in args.responses:
        response_path = resolve_path(configured_path)
        report = evaluate(manifest, load_jsonl(response_path), response_path, args.max_errors)
        reports[response_path.stem] = report
        print(
            f"{response_path.name}: exact={report['exact_match']:.4f}, "
            f"P/R/F1={report['micro_precision']:.4f}/"
            f"{report['micro_recall']:.4f}/{report['micro_f1']:.4f}, "
            f"format_error={report['format_error_rate']:.4f}"
        )

    output = resolve_path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as file:
        json.dump({"manifest": str(manifest_path), "models": reports}, file,
                  ensure_ascii=False, indent=2)
    print(f"✅ 指标与错误样本已保存: {output}")


if __name__ == "__main__":
    main()
