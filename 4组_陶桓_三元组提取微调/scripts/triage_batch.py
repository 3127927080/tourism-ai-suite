"""批量初筛：把多条句子合并为一次 API 调用，用 12 类 schema 抽取。

从通用数据随机抽 N 条（默认 2000），每批 BATCH 句一次请求，要求返回严格 JSON。
解析失败时整批回退为单句调用，保证不丢数据。

用法:
    python scripts/triage_batch.py --sample 2000 --batch 8 --workers 8
"""

from __future__ import annotations

import argparse
import json
import random
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
OUT_FILE = ROOT / "data" / "triage" / "triage_batch2000.jsonl"

RELATIONS = ["位于", "属于", "包含", "途经", "推荐", "适合", "需要", "提供", "邻近", "以…闻名", "建议", "对比"]
SYSTEM_PROMPT = (
    "你是信息抽取引擎。用户会给出若干条编号文本，请为每条文本抽取实体关系三元组。\n"
    f"关系必须从以下列表中选择：{'、'.join(RELATIONS)}。\n"
    "头实体与尾实体必须是原文中出现的连续子串；没有符合的三元组则该文本下留空。\n"
    '严格按下面格式输出，不要 JSON、不要解释：\n'
    "【文本 1】\n1. （头实体，关系，尾实体）\n【文本 2】\n（头实体，关系，尾实体）"
)

SINGLE_SYSTEM_PROMPT = (
    "你是信息抽取引擎。请从句子中抽取实体关系三元组（头实体, 关系, 尾实体）。\n"
    f"关系仅限：{'、'.join(RELATIONS)}。\n"
    "头实体与尾实体必须是原句中出现的连续子串；没有符合的只输出：无三元组。\n"
    "否则每条三元组独占一行，严格格式：（头实体，关系，尾实体），不要编号。"
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


def normalize(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def clean_triples(items, sentence: str) -> list[list[str]]:
    norm_s = normalize(sentence)
    out, seen = [], set()
    if not isinstance(items, list):
        return out
    for it in items:
        if not (isinstance(it, (list, tuple)) and len(it) == 3):
            continue
        head, rel, tail = (str(x).strip() for x in it)
        if not head or not tail or rel not in RELATIONS:
            continue
        if normalize(head) not in norm_s or normalize(tail) not in norm_s:
            continue
        key = (head, rel, tail)
        if key in seen:
            continue
        seen.add(key)
        out.append([head, rel, tail])
    return out


def parse_segments(text: str) -> dict[int, list]:
    """解析 【文本 k】 分段格式，返回 {k: [[h,r,t],...]}（未校验）。"""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I).strip()
    marks = list(re.finditer(r"【\s*文本\s*(\d+)\s*】", text))
    result: dict[int, list] = {}
    for i, m in enumerate(marks):
        k = int(m.group(1))
        body = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        items = []
        for pm in re.finditer(r"[（(]\s*([^（()）]+?)\s*[)）]", body):
            parts = re.split(r"\s*[,，]\s*", pm.group(1))
            if len(parts) == 3:
                items.append([p.strip() for p in parts])
        result[k] = items
    return result


def parse_json_obj(text: str) -> dict | None:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I).strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def batch_call(client, model, batch: list[tuple[int, str]], retries: int = 3):
    """返回 [(idx, sentence, raw, triples, status)]"""
    parts = [f"【文本 {k}】{s[:300]}" for k, (_, s) in enumerate(batch, 1)]
    user = "请抽取下列文本中的三元组：\n\n" + "\n".join(parts)
    last = ""
    for attempt in range(retries):
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": user}],
                temperature=0.1,
            )
            raw = r.choices[0].message.content or ""
            obj = parse_json_obj(raw)
            if obj is not None:
                seg_map = {int(k): v for k, v in obj.items() if str(k).strip().isdigit()}
            else:
                seg_map = parse_segments(raw)
            if not seg_map:
                raise ValueError("无法解析（JSON 与分段格式均失败）")
            results = []
            for k, (idx, sent) in enumerate(batch, 1):
                triples = clean_triples(seg_map.get(k, []), sent)
                status = "keep" if triples else "none"
                results.append((idx, sent, raw, triples, status))
            return results
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(min(2 ** attempt, 6))

    # 整批失败 -> 逐条单句回退
    results = []
    for idx, sent in batch:
        results.append(single_call(client, model, idx, sent))
    return results


def single_call(client, model, idx: int, sent: str, retries: int = 2):
    last = ""
    for attempt in range(retries):
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SINGLE_SYSTEM_PROMPT},
                          {"role": "user", "content": f"请抽取以下句子中的三元组：\n句子：{sent[:400]}"}],
                temperature=0.1,
            )
            raw = r.choices[0].message.content or ""
            items = []
            for pm in re.finditer(r"[（(]\s*([^（()）]+?)\s*[)）]", raw):
                parts = re.split(r"\s*[,，]\s*", pm.group(1))
                if len(parts) == 3:
                    items.append([p.strip() for p in parts])
            triples = clean_triples(items, sent)
            return (idx, sent, raw, triples, "keep" if triples else "none")
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(min(2 ** attempt, 6))
    return (idx, sent, "", [], "error", last)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=2000)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--output", default=str(OUT_FILE))
    args = ap.parse_args()

    env = load_env()
    client = OpenAI(api_key=env.get("LLM_API_KEY") or env.get("ALIYUN_API_KEY"),
                    base_url=env.get("LLM_API_BASE"), timeout=120, max_retries=0)
    model = env.get("LLM_DEFAULT_MODEL")

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
    random.Random(args.seed).shuffle(rows)
    rows = rows[: args.sample]
    print(f"model={model} sample={len(rows)} batch={args.batch} workers={args.workers}")

    batches = [rows[i:i + args.batch] for i in range(0, len(rows), args.batch)]

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
    if done:
        batches = [b for b in batches if not all(i in done for i, _ in b)]
        print(f"断点续跑：已完成 {len(done)} 条")

    stats = {"keep": 0, "none": 0, "error": 0}
    t0 = time.time()
    total = sum(len(b) for b in batches)
    n_done = 0
    with out_path.open("a", encoding="utf-8") as fout, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(batch_call, client, model, b) for b in batches]
        for n, fut in enumerate(as_completed(futures), 1):
            for item in fut.result():
                idx, sent, raw, triples, status = item[0], item[1], item[2], item[3], item[4]
                rec = {"idx": idx, "sentence": sent, "raw": raw, "triples": triples, "status": status}
                if status == "error":
                    rec["error"] = item[5]
                stats[status] = stats.get(status, 0) + 1
                with _write_lock:
                    fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_done += 1
            with _write_lock:
                fout.flush()
            if n % 20 == 0 or n == len(futures):
                el = time.time() - t0
                rate = sum(stats.values()) / el if el else 0
                eta = (total - sum(stats.values())) / rate / 60 if rate else 0
                print(f"[批 {n}/{len(futures)}] 条 {sum(stats.values())}/{total} {stats} "
                      f"{rate*60:.0f} 条/分 ETA {eta:.1f} 分")

    print("完成:", stats)


if __name__ == "__main__":
    main()
