"""诊断 gold 稳定性：对 gold 文本再抽 2 次，计算与原 gold 的一致性。

产出:
    data/sft/gold_vote.jsonl     >=2 票一致的三元组（可选作更稳的 gold）
    data/sft/gold_stability.json 一致性统计
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_tourism_flywheel import load_env, make_client
from eval_baseline_llm import SYSTEM_PROMPT
from eval_triples import parse_triples

GOLD = ROOT / "data" / "sft" / "gold.jsonl"
OUT_VOTE = ROOT / "data" / "sft" / "gold_vote.jsonl"
OUT_STAT = ROOT / "data" / "sft" / "gold_stability.json"


def call(client, model, query):
    r = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": query}],
        temperature=0.7,
    )
    return parse_triples(r.choices[0].message.content or "").triples


def main() -> None:
    env = load_env()
    client, model, provider = make_client(env)
    rows = [json.loads(l) for l in open(GOLD, encoding="utf-8") if l.strip()]
    print(f"provider={provider} gold={len(rows)}")

    stats, vote_rows = [], []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = []
        for row in rows:
            futs.append((row, pool.submit(call, client, model, row["query"]),
                         pool.submit(call, client, model, row["query"])))
        for n, (row, f1, f2) in enumerate(futs, 1):
            v1, v2 = f1.result(), f2.result()
            gold = parse_triples(row["gold"]).triples
            all3 = gold | v1 | v2
            votes = {}
            for t in all3:
                votes[t] = int(t in gold) + int(t in v1) + int(t in v2)
            agreed = {t for t, c in votes.items() if c >= 2}
            stats.append({
                "id": row["id"],
                "gold_n": len(gold), "v1_n": len(v1), "v2_n": len(v2),
                "gold_v1_jacc": len(gold & v1) / len(gold | v1) if gold | v1 else 1.0,
                "gold_v2_jacc": len(gold & v2) / len(gold | v2) if gold | v2 else 1.0,
                "v1_v2_jacc": len(v1 & v2) / len(v1 | v2) if v1 | v2 else 1.0,
                "agreed_n": len(agreed),
            })
            vote_rows.append({
                "id": row["id"], "query": row["query"],
                "gold": "\n".join(f"({h}, {r}, {t})" for h, r, t in agreed) or "无三元组",
            })
            if n % 50 == 0:
                print(f"  {n}/{len(rows)}")

    def avg(k):
        return round(sum(s[k] for s in stats) / len(stats), 4)

    summary = {
        "samples": len(stats),
        "avg_jaccard_gold_v1": avg("gold_v1_jacc"),
        "avg_jaccard_gold_v2": avg("gold_v2_jacc"),
        "avg_jaccard_v1_v2": avg("v1_v2_jacc"),
        "avg_gold_triples": round(sum(s["gold_n"] for s in stats) / len(stats), 2),
        "avg_agreed_triples": round(sum(s["agreed_n"] for s in stats) / len(stats), 2),
    }
    OUT_STAT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with OUT_VOTE.open("w", encoding="utf-8") as f:
        for r in vote_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
