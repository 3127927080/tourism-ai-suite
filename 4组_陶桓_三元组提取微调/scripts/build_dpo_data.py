"""构造 DPO 偏好数据（规则生成 rejected，秒级完成）。

prompt   = system + user（与 SFT 完全一致）
chosen   = 清洗后的 gold 三元组
rejected = 混合四类错误：漏抽 / 关系错误 / 实体幻觉 / 格式错误

用法:
    python scripts/build_dpo_data.py --source data/sft_clean/train.jsonl \
        --output data/dpo/train.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
RELATIONS = ["位于", "属于", "包含", "途经", "推荐", "适合", "需要", "提供", "邻近", "以…闻名", "建议", "对比"]


def parse_answer(text: str) -> list[tuple[str, str, str]]:
    import re
    out = []
    for m in re.finditer(r"[（(]\s*([^（()）]+?)\s*[)）]", text or ""):
        parts = [p.strip() for p in re.split(r"\s*[,，]\s*", m.group(1))]
        if len(parts) == 3:
            out.append(tuple(parts))  # type: ignore[arg-type]
    return out


def fmt(triples) -> str:
    return "\n".join(f"({h}, {r}, {t})" for h, r, t in triples)


def make_rejected(sentence: str, triples, rng: random.Random) -> tuple[str, str]:
    """返回 (rejected 文本, 错误类型)

    只构造「质量错误」：关系错误 / 实体幻觉 / 格式错误。
    故意不使用「漏抽」——它会让模型学到「多输出 = 更好」，在 4B 上实测导致精确率暴跌。
    """
    kind = rng.choice(["relation", "entity", "format"])
    if not triples:
        # 原文无三元组时，只能造"幻觉"或"格式"
        kind = rng.choice(["hallucinate", "format"])

    if kind == "relation" and triples:
        out = []
        for h, r, t in triples:
            others = [x for x in RELATIONS if x != r]
            out.append((h, rng.choice(others), t))
        return fmt(out), "关系错误"

    if kind in ("entity", "hallucinate"):
        base = list(triples) or [("某景点", "位于", "某城市")]
        out = []
        for h, r, t in base:
            if rng.random() < 0.5:
                h = h + rng.choice(["市区", "景区", "周边"])
            else:
                t = t + rng.choice(["附近", "一带", "片区"])
            out.append((h, r, t))
        return fmt(out), "实体幻觉"

    # format：编号/markdown 污染
    body = fmt(triples) if triples else "(某景点, 位于, 某城市)"
    return "1. " + body.replace("\n", "\n2. ") + "\n以上为抽取结果。", "格式错误"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(ROOT / "data/sft_clean/train.jsonl"))
    ap.add_argument("--output", default=str(ROOT / "data/dpo/train.jsonl"))
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    rows = [json.loads(l) for l in open(args.source, encoding="utf-8") if l.strip()]
    out, kinds = [], Counter()
    for row in rows:
        msgs = row["messages"]
        prompt = msgs[:2]              # system + user
        chosen = msgs[2]["content"]
        triples = parse_answer(chosen)
        rejected, kind = make_rejected(msgs[1]["content"], triples, rng)
        if rejected.strip() == chosen.strip():
            continue
        kinds[kind] += 1
        out.append({
            "prompt": prompt,
            "chosen": [{"role": "assistant", "content": chosen}],
            "rejected": [{"role": "assistant", "content": rejected}],
        })

    rng.shuffle(out)
    split = int(len(out) * 0.95)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dev_path = out_path.with_name(out_path.stem + "_dev" + out_path.suffix)

    for path, data in [(out_path, out[:split]), (dev_path, out[split:])]:
        with path.open("w", encoding="utf-8") as f:
            for r in data:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{path.name}: {len(data)} 条")

    print("错误类型分布:", dict(kinds))


if __name__ == "__main__":
    main()
