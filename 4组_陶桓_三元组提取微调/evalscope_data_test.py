"""构建带 gold 的三元组评估清单。

默认从 canonical test messages 数据读取，不覆盖旧的 query-only 评估文件。
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_FILE = SCRIPT_DIR / "data" / "test_triple_converted.jsonl"
DEFAULT_OUTPUT_FILE = SCRIPT_DIR / "data" / "eval_questions_with_gold.jsonl"


def resolve_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return SCRIPT_DIR / candidate


def load_manifest_records(input_path: Path) -> list[dict]:
    records = []
    with input_path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            if not line.strip():
                continue
            data = json.loads(line)
            messages = data.get("messages", [])
            user_messages = [m.get("content") for m in messages if m.get("role") == "user"]
            assistant_messages = [m.get("content") for m in messages if m.get("role") == "assistant"]
            if not user_messages or not assistant_messages:
                raise ValueError(f"{input_path}:{line_number} 缺少 user 或 assistant 消息")
            records.append({
                "id": line_number - 1,
                "query": user_messages[-1],
                "gold": assistant_messages[-1],
            })
    return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_INPUT_FILE,
                        help="包含 messages 的 canonical JSONL 数据")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_FILE)
    parser.add_argument("--sample-size", type=int, default=-1,
                        help="抽取数量，-1 表示全部；问题和 gold 一起抽样")
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = resolve_path(args.source)
    output = resolve_path(args.output)
    records = load_manifest_records(source)
    print(f"总问题数: {len(records)}（来源: {source}）")

    if 0 < args.sample_size < len(records):
        records = random.Random(args.seed).sample(records, args.sample_size)
    elif args.sample_size == 0:
        records = []
    elif args.sample_size < -1:
        raise ValueError("--sample-size 必须为 -1 或非负整数")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"实际输出问题数: {len(records)}")
    print(f"✅ 问题集已保存至: {output}")
    if records:
        print("示例第一条:")
        print(json.dumps(records[0], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
