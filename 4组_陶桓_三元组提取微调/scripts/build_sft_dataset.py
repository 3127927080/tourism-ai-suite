"""合并真实 chunk 标注 + 飞轮生成数据，产出正式 SFT 数据集。

输入:
    data/real/real_chunks.jsonl      （build_tourism_real.py 产物）
    data/flywheel/flywheel_raw.jsonl （build_tourism_flywheel.py 产物）
输出:
    data/sft/train.jsonl / dev.jsonl  （messages 格式，含 12 类 system prompt）

规则:
    - 只取 status=keep 的样本
    - 按句子去重
    - 按来源分层后 9:1 划分 train/dev
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
REAL = ROOT / "data" / "real" / "real_chunks.jsonl"
FLY = ROOT / "data" / "flywheel" / "flywheel_raw.jsonl"
OUT = ROOT / "data" / "sft"

SYSTEM_PROMPT = (
    "你是旅游领域信息抽取专家，请从句子中抽取实体关系三元组（头实体, 关系, 尾实体）。"
    "可选关系：位于、属于、包含、途经、推荐、适合、需要、提供、邻近、以…闻名、建议、对比。"
)


def to_messages(text: str, triples) -> dict:
    answer = "\n".join(f"({h}, {r}, {t})" for h, r, t in triples)
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"请抽取以下句子中的三元组：\n句子：{text}"},
        {"role": "assistant", "content": answer},
    ]}


def load_keep(path: Path, text_key: str) -> list[tuple[str, list]]:
    if not path.exists():
        print(f"⚠ 缺失输入: {path}")
        return []
    out = []
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        status = r.get("status")
        # 飞轮产物没有 status 字段，以 triples 非空为准
        if status is not None and status != "keep":
            continue
        if not r.get("triples"):
            continue
        text = str(r.get(text_key, "")).strip()
        if text:
            out.append((text, r["triples"]))
    return out


def main() -> None:
    real = load_keep(REAL, "text")
    fly = load_keep(FLY, "sentence")
    print(f"真实 chunk keep: {len(real)}  飞轮 keep: {len(fly)}")

    # 从真实 chunk 中划出 gold（不参与训练），保证评估集与训练集同分布但无重叠
    rng = random.Random(42)
    rng.shuffle(real)
    gold_n = 200 if len(real) >= 500 else max(0, len(real) // 5)
    gold_rows, real_train = real[:gold_n], real[gold_n:]
    print(f"gold: {len(gold_rows)} 条（来自真实 chunk，不入训练）")

    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "gold.jsonl").open("w", encoding="utf-8") as f:
        for i, (text, triples) in enumerate(gold_rows):
            f.write(json.dumps({
                "id": i,
                "query": f"请抽取以下句子中的三元组：\n句子：{text}",
                "gold": "\n".join(f"({h}, {r}, {t})" for h, r, t in triples),
            }, ensure_ascii=False) + "\n")
    print(f"  gold.jsonl: {len(gold_rows)} 条")

    seen, rows = set(), []
    for text, triples in real_train + fly:
        key = text.strip()
        if key in seen:
            continue
        seen.add(key)
        rows.append((text, triples))

    rng.shuffle(rows)
    split = int(len(rows) * 0.9)
    train, dev = rows[:split], rows[split:]

    def dump(path: Path, data):
        with path.open("w", encoding="utf-8") as f:
            for text, triples in data:
                f.write(json.dumps(to_messages(text, triples), ensure_ascii=False) + "\n")
        print(f"  {path.name}: {len(data)} 条")

    dump(OUT / "train.jsonl", train)
    dump(OUT / "dev.jsonl", dev)

    # 关系统计
    from collections import Counter
    cnt = Counter(t[1] for _, triples in rows for t in triples)
    print("关系分布:", dict(cnt.most_common()))


if __name__ == "__main__":
    main()
