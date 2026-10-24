"""清洗训练标签：对每条训练样本再抽 2 次，与原标签做 3 票投票，保留 >=2 票一致的三元组。

目的：让训练标签达到与评估 gold 相同的"多票一致"质量，降低单次抽取噪声。

用法:
    python scripts/clean_train_labels.py --workers 8
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

OUT_DIR = ROOT / "data" / "sft_clean"


def call(client, model, query):
    r = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": query}],
        temperature=0.7,
    )
    return parse_triples(r.choices[0].message.content or "").triples


def clean_split(client, model, in_path: Path, out_path: Path, workers: int) -> dict:
    rows = [json.loads(l) for l in in_path.open(encoding="utf-8") if l.strip()]
    print(f"{in_path.name}: {len(rows)} 条")
    out, stats = [], {"kept_samples": 0, "empty_samples": 0, "orig_triples": 0, "clean_triples": 0}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = []
        for row in rows:
            msgs = row["messages"]
            query = msgs[1]["content"]
            orig = parse_triples(msgs[2]["content"]).triples
            futs.append((row, orig, pool.submit(call, client, model, query),
                         pool.submit(call, client, model, query)))
        for n, (row, orig, f1, f2) in enumerate(futs, 1):
            v1, v2 = f1.result(), f2.result()
            votes = {}
            for t in (orig | v1 | v2):
                votes[t] = int(t in orig) + int(t in v1) + int(t in v2)
            kept = [t for t, c in votes.items() if c >= 2]
            stats["orig_triples"] += len(orig)
            stats["clean_triples"] += len(kept)
            if kept:
                stats["kept_samples"] += 1
                msgs = row["messages"]
                msgs[2]["content"] = "\n".join(f"({h}, {r}, {t})" for h, r, t in kept)
            else:
                stats["empty_samples"] += 1
                msgs = row["messages"]
                msgs[2]["content"] = "无三元组"
            out.append(row)
            if n % 100 == 0:
                print(f"  {n}/{len(rows)}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return stats


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    env = load_env()
    client, model, provider = make_client(env)
    print(f"provider={provider} model={model}")

    s_tr = clean_split(client, model, ROOT / "data/sft/train.jsonl", OUT_DIR / "train.jsonl", args.workers)
    s_dv = clean_split(client, model, ROOT / "data/sft/dev.jsonl", OUT_DIR / "dev.jsonl", args.workers)

    print("train:", s_tr)
    print("dev:", s_dv)


if __name__ == "__main__":
    main()
