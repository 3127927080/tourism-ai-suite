"""对 30 份旅游文档的真实 chunk 做三元组标注（官方 DeepSeek）。

切分逻辑复刻知识库 node_document_split：
  按 Markdown 标题切分 -> >500 字再按句切分（chunk_size=500, overlap=50）

用法:
    python scripts/build_tourism_real.py --workers 8
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

from langchain_text_splitters import RecursiveCharacterTextSplitter

# -I 隔离模式不会把脚本目录加入 sys.path，这里显式补上，便于 import 同目录模块
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_tourism_flywheel import load_env, make_client, RELATIONS

ROOT = Path(__file__).resolve().parent.parent
DOC_ROOT = Path(r"C:\Users\th\Desktop\4组_陶桓_实战\4组_陶桓_旅游_知识库\旅游数据")
OUT_DIR = ROOT / "data" / "real"

SYSTEM_PROMPT = (
    "你是旅游知识库的信息抽取引擎。请从给定文本中抽取实体关系三元组。\n"
    f"关系只能从以下 12 个词中逐字选用：{'、'.join(RELATIONS)}。\n"
    "表示所属城市/片区用「位于」，表示类型/类别用「属于」。\n"
    "头实体、尾实体必须是原文中出现的连续子串；没有符合的只输出：无三元组。\n"
    "否则每行一条，严格格式：（头实体，关系，尾实体），不要编号、不要解释。"
)

_write_lock = threading.Lock()


def split_doc(text: str) -> list[str]:
    """按知识库 node_document_split 的规则切 chunk。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"^##\s*元数据\s*$\n.*?(?=^##\s|\Z)", "", text, flags=re.M | re.S)
    lines = text.split("\n")
    secs, cur = [], 0
    for i, line in enumerate(lines):
        if i > 0 and re.match(r"^\s*#{1,6}\s+.+", line.strip()):
            block = "\n".join(lines[cur:i])
            if block.strip():
                secs.append(block)
            cur = i
    tail = "\n".join(lines[cur:])
    if tail.strip():
        secs.append(tail)

    sp = RecursiveCharacterTextSplitter(
        separators=["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " "],
        chunk_size=500, chunk_overlap=50,
    )
    chunks = []
    for sec in secs:
        body = sec.lstrip("#").strip()
        if len(body) <= 500 or "<table" in body:
            chunks.append(sec.strip())
        else:
            chunks.extend(sp.split_text(body))
    return [c for c in chunks if len(c.strip()) >= 10]


def normalize(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def clean_triples(raw: str, text: str) -> list[list[str]]:
    norm_t = normalize(text)
    out, seen = [], set()
    for pm in re.finditer(r"[（(]\s*([^（()）]+?)\s*[)）]", raw or ""):
        parts = re.split(r"\s*[,，]\s*", pm.group(1))
        if len(parts) != 3:
            continue
        head, rel, tail = (p.strip() for p in parts)
        if not head or not tail or rel not in RELATIONS:
            continue
        if normalize(head) not in norm_t or normalize(tail) not in norm_t:
            continue
        key = (head, rel, tail)
        if key in seen:
            continue
        seen.add(key)
        out.append([head, rel, tail])
    return out


def call_one(client, model, idx, text, source, retries=3):
    for attempt in range(retries):
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": f"请抽取以下文本中的三元组：\n{text[:800]}"}],
                temperature=0.2,
            )
            raw = r.choices[0].message.content or ""
            triples = clean_triples(raw, text)
            return {"idx": idx, "source": source, "text": text, "raw": raw,
                    "triples": triples, "status": "keep" if triples else "none"}
        except Exception as e:
            if attempt == retries - 1:
                return {"idx": idx, "source": source, "text": text, "raw": "",
                        "triples": [], "status": "error", "error": f"{type(e).__name__}: {e}"}
            time.sleep(min(2 ** attempt, 6))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--output", default=str(OUT_DIR / "real_chunks.jsonl"))
    args = ap.parse_args()

    env = load_env()
    client, model, provider = make_client(env)

    items = []
    for md in sorted(DOC_ROOT.glob("**/*.md")):
        for chunk in split_doc(md.read_text(encoding="utf-8")):
            items.append((len(items), chunk, str(md.relative_to(DOC_ROOT))))
    print(f"provider={provider} model={model} chunk 数={len(items)}")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out_path.exists():
        for line in out_path.open(encoding="utf-8"):
            try:
                done.add(json.loads(line)["idx"])
            except Exception:
                pass
    todo = [x for x in items if x[0] not in done]
    print(f"已完成 {len(done)}，本次待处理 {len(todo)}")

    stats = {"keep": 0, "none": 0, "error": 0}
    t0 = time.time()
    with out_path.open("a", encoding="utf-8") as fout, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(call_one, client, model, i, t, s) for i, t, s in todo]
        for n, fut in enumerate(as_completed(futs), 1):
            res = fut.result()
            stats[res["status"]] = stats.get(res["status"], 0) + 1
            with _write_lock:
                fout.write(json.dumps(res, ensure_ascii=False) + "\n")
            if n % 50 == 0 or n == len(todo):
                el = time.time() - t0
                rate = n / el if el else 0
                print(f"[{n}/{len(todo)}] {stats} {rate*60:.0f} 条/分 ETA {(len(todo)-n)/rate/60:.1f} 分")
    print("完成:", stats)


if __name__ == "__main__":
    main()
