"""用 happycode 网关（deepseek-flash）对通用三元组数据做初筛。

目标：把开放域三元组数据映射到旅游 12 类关系。
  - 至少 1 个合法三元组 -> keep（可转 SFT）
  - 明确无线索        -> none（可当负样本）
  - 输出无法解析      -> bad

支持断点续跑：已处理的 id 会跳过。

用法:
    python scripts/triage_happycode.py --limit 20 --workers 8
"""

from __future__ import annotations

import argparse
import json
import re
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

from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
ENV = Path(r"C:\Users\th\Desktop\4组_陶桓_实战\4组_陶桓_旅游_知识库\.env")
IN_DATA = ROOT / "data" / "train_triple_converted.jsonl"
OUT_DIR = ROOT / "data" / "triage"
OUT_FILE = OUT_DIR / "triage_happycode.jsonl"

RELATIONS = ["位于", "属于", "包含", "途经", "推荐", "适合", "需要", "提供", "邻近", "以…闻名", "建议", "对比"]
SYSTEM_PROMPT = (
    "你是旅游领域信息抽取专家。请从句子中抽取实体关系三元组（头实体, 关系, 尾实体）。\n"
    f"可选关系仅限：{'、'.join(RELATIONS)}。\n"
    "要求：\n"
    "1. 头实体和尾实体必须是原句中出现的连续子串；\n"
    "2. 如果句子中没有符合上述关系的信息，只输出：无三元组；\n"
    "3. 否则每条三元组独占一行，严格格式：(头, 关系, 尾)，不要编号、不要解释。"
)

_write_lock = threading.Lock()


def load_env() -> dict:
    vals = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        vals[k.strip()] = v.strip()
    return vals


def extract_sentence(user_content: str) -> str:
    if "句子：" in user_content:
        return user_content.split("句子：", 1)[1].strip()
    return user_content.strip()


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def parse_triples(raw: str, sentence: str) -> tuple[list[list[str]], str]:
    """返回 (合法三元组列表, 状态)。状态: keep/none/bad"""
    if not raw:
        return [], "bad"
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.S | re.I).strip()
    if "无三元组" in text or text.strip() in {"无", "没有"}:
        return [], "none"

    norm_sentence = normalize(sentence)
    triples: list[list[str]] = []
    bad = False
    for m in re.finditer(r"[（(]\s*([^（()）]+?)\s*[)）]", text):
        seg = m.group(1)
        parts = re.split(r"\s*[,，]\s*", seg)
        if len(parts) != 3:
            bad = True
            continue
        head, rel, tail = (p.strip() for p in parts)
        if not head or not tail or rel not in RELATIONS:
            bad = True
            continue
        # 实体落地校验
        if normalize(head) not in norm_sentence or normalize(tail) not in norm_sentence:
            bad = True
            continue
        triples.append([head, rel, tail])

    dedup, seen = [], set()
    for t in triples:
        key = tuple(t)
        if key not in seen:
            seen.add(key)
            dedup.append(t)

    if dedup:
        return dedup, "keep"
    return [], ("bad" if bad else "bad")


def call_one(client: OpenAI, model: str, idx: int, sentence: str, retries: int = 3) -> dict:
    last_err = ""
    for attempt in range(retries):
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"请抽取以下句子中的三元组：\n句子：{sentence}"},
                ],
                temperature=0.1,
            )
            raw = (r.choices[0].message.content or "").strip()
            triples, status = parse_triples(raw, sentence)
            return {
                "idx": idx,
                "sentence": sentence,
                "raw": raw,
                "triples": triples,
                "status": status,
                "usage": {
                    "prompt": r.usage.prompt_tokens,
                    "completion": r.usage.completion_tokens,
                },
            }
        except Exception as e:  # 网络/限流
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(min(2 ** attempt, 8))
    return {"idx": idx, "sentence": sentence, "raw": "", "triples": [], "status": "error", "error": last_err}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 表示全量")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--output", default=str(OUT_FILE))
    args = ap.parse_args()

    env = load_env()
    client = OpenAI(
        api_key=env.get("LLM_API_KEY") or env.get("ALIYUN_API_KEY"),
        base_url=env.get("LLM_API_BASE"),
        timeout=90,
        max_retries=0,
    )
    model = env.get("LLM_DEFAULT_MODEL")
    print(f"model={model} workers={args.workers}")

    rows = []
    with IN_DATA.open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            rec = json.loads(line)
            msgs = rec.get("messages") or []
            if len(msgs) < 3:
                continue
            rows.append((i, extract_sentence(msgs[1].get("content", ""))))
    if args.limit:
        rows = rows[: args.limit]

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out_path.exists():
        with out_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["idx"])
                except Exception:
                    pass
    todo = [(i, s) for i, s in rows if i not in done]
    print(f"总数 {len(rows)}，已完成 {len(done)}，本次待处理 {len(todo)}")

    stats = {"keep": 0, "none": 0, "bad": 0, "error": 0}
    t0 = time.time()
    with out_path.open("a", encoding="utf-8") as fout, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(call_one, client, model, i, s): i for i, s in todo}
        for n, fut in enumerate(as_completed(futures), 1):
            res = fut.result()
            stats[res["status"]] = stats.get(res["status"], 0) + 1
            with _write_lock:
                fout.write(json.dumps(res, ensure_ascii=False) + "\n")
                fout.flush()
            if n % 20 == 0 or n == len(todo):
                elapsed = time.time() - t0
                rate = n / elapsed if elapsed else 0
                eta = (len(todo) - n) / rate / 60 if rate else 0
                print(f"[{n}/{len(todo)}] {stats}  {rate*60:.1f} 条/分  ETA {eta:.1f} 分")

    print("完成:", stats)


if __name__ == "__main__":
    main()
