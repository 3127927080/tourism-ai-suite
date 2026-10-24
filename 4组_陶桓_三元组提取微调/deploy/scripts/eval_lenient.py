"""宽松评估：实体按包含/归一化匹配，关系需完全一致。

同一实体的措辞差异（"把高铁接驳串起来" vs "高铁接驳"）不应被判为完全错误。

用法:
    python scripts/eval_lenient.py --manifest data/sft/gold_vote.jsonl \
        --responses data/sft/responses_sft_1.7b.jsonl data/sft/responses_baseline_llm.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eval_triples import parse_triples, load_jsonl


def ent_match(a: str, b: str) -> bool:
    """实体宽松匹配：归一化后互相包含即认为匹配。"""
    if a == b:
        return True
    if len(a) < 2 or len(b) < 2:
        return a == b
    return a in b or b in a


def triple_match(p, g) -> bool:
    return p[1] == g[1] and ent_match(p[0], g[0]) and ent_match(p[2], g[2])


def evaluate_lenient(manifest, responses) -> dict:
    tp = fp = fn = 0
    for gold_rec, pred_rec in zip(manifest, responses):
        gold = parse_triples(str(gold_rec.get("gold", ""))).triples
        pred = parse_triples(str(pred_rec.get("response", ""))).triples
        matched_gold = set()
        for p in pred:
            hit = None
            for g in gold:
                if g in matched_gold:
                    continue
                if triple_match(p, g):
                    hit = g
                    break
            if hit is not None:
                matched_gold.add(hit)
                tp += 1
            else:
                fp += 1
        fn += len(gold) - len(matched_gold)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(ROOT / "data" / "sft" / "gold_vote.jsonl"))
    ap.add_argument("--responses", nargs="+", required=True)
    args = ap.parse_args()

    manifest = load_jsonl(Path(args.manifest))
    print(f"manifest={args.manifest} ({len(manifest)} 条)  宽松匹配")
    for rp in args.responses:
        resp = load_jsonl(Path(rp))
        if len(resp) != len(manifest):
            print(f"⚠ {Path(rp).name}: 条数 {len(resp)} != {len(manifest)}，跳过")
            continue
        r = evaluate_lenient(manifest, resp)
        print(f"{Path(rp).name}: P/R/F1={r['precision']}/{r['recall']}/{r['f1']}  (tp={r['tp']}, fp={r['fp']}, fn={r['fn']})")


if __name__ == "__main__":
    main()
