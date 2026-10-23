"""DeepSeek 直调基线：对 gold 集逐条抽取，产出 responses 供 eval_triples 对比。

用法:
    python scripts/eval_baseline_llm.py --workers 8
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_tourism_flywheel import load_env, make_client

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "data" / "sft" / "gold.jsonl"
OUT = ROOT / "data" / "sft" / "responses_baseline_llm.jsonl"

SYSTEM_PROMPT = (
    "你是旅游领域信息抽取专家，请从句子中抽取实体关系三元组（头实体, 关系, 尾实体）。"
    "可选关系：位于、属于、包含、途经、推荐、适合、需要、提供、邻近、以…闻名、建议、对比。"
)

_lock = threading.Lock()


def call_one(client, model, row, retries=3):
    for attempt in range(retries):
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": row["query"]}],
                temperature=0.0,
            )
            content = (r.choices[0].message.content or "").strip()
            return {"id": row["id"], "query": row["query"], "response": content}
        except Exception:
            if attempt == retries - 1:
                return {"id": row["id"], "query": row["query"], "response": ""}
            time.sleep(min(2 ** attempt, 6))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    env = load_env()
    client, model, provider = make_client(env)
    rows = [json.loads(l) for l in open(GOLD, encoding="utf-8") if l.strip()]
    print(f"provider={provider} model={model} gold={len(rows)}")

    out = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(call_one, client, model, r) for r in rows]
        for n, fut in enumerate(as_completed(futs), 1):
            out.append(fut.result())
            if n % 50 == 0:
                print(f"  {n}/{len(rows)}")

    out.sort(key=lambda x: x["id"])
    with OUT.open("w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"✅ {OUT} ({len(out)} 条)")


if __name__ == "__main__":
    main()
