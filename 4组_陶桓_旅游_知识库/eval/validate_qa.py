"""校验 eval/qa.csv 的题目与标准答案能否在旅游文档中找到依据。

做法：
1. 把标准答案与 36 份 Markdown 都归一化（只保留中文与字母数字，去掉空白和标点）；
2. 用 12 字的滑窗在文档里做子串匹配，统计每条答案被哪份文档覆盖的比例；
3. 命中比例达到阈值才算可溯源，并输出命中的 source_file；
4. 顺带确认该 source_file 确实在 Milvus 的 chunks 集合里（可 --no-milvus 跳过）。

用法：
    uv run python eval/validate_qa.py
    uv run python eval/validate_qa.py --min-ratio 0.5 --window 12
"""
import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "旅游数据"
NORMALIZE_PATTERN = re.compile(r"[^0-9A-Za-z一-鿿]+")


def normalize(text: str) -> str:
    """去掉空白与标点，只保留可用于比对的实词字符。"""
    return NORMALIZE_PATTERN.sub("", text or "")


def load_corpus() -> dict[str, str]:
    """读取 36 份文档，返回 {相对路径: 归一化正文}。"""
    corpus = {}
    for path in sorted(DATA_DIR.rglob("*.md")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        corpus[str(path.relative_to(DATA_DIR)).replace("\\", "/")] = normalize(text)
    return corpus


def make_windows(text: str, window: int) -> list[str]:
    """按固定步长切出比对窗口；文本短于窗口时整条比对。"""
    if len(text) <= window:
        return [text] if text else []
    step = max(1, window // 3)
    windows = [text[i:i + window] for i in range(0, len(text) - window + 1, step)]
    return windows


def best_match(ground_truth: str, corpus: dict[str, str], window: int) -> tuple[str, int, int]:
    """返回（最佳命中文档, 命中窗口数, 窗口总数）。"""
    windows = make_windows(normalize(ground_truth), window)
    if not windows:
        return "", 0, 0
    counter: Counter[str] = Counter()
    for source, text in corpus.items():
        hits = sum(1 for w in windows if w in text)
        if hits:
            counter[source] = hits
    if not counter:
        return "", 0, len(windows)
    source, hits = counter.most_common(1)[0]
    return source, hits, len(windows)


def milvus_has_source(source_file: str) -> bool:
    """确认该文档已进入 chunks 集合。"""
    from atguigu.config.config import MilvusConfig
    from atguigu.tool.milvus_client_tool import get_milvus_client

    client = get_milvus_client()
    rows = client.query(
        collection_name=MilvusConfig.chunks_collection,
        filter=f'source_file == "{source_file}"',
        output_fields=["count(*)"],
    )
    return bool(rows) and int(list(rows[0].values())[0]) > 0


def main() -> int:
    parser = argparse.ArgumentParser(description="校验旅游 QA 的可溯源性")
    parser.add_argument("--qa", default=str(EVAL_DIR / "qa.csv"), help="待校验的 QA 文件")
    parser.add_argument("--window", type=int, default=12, help="滑窗长度（字）")
    parser.add_argument("--min-ratio", type=float, default=0.4, help="命中比例阈值，默认 0.4")
    parser.add_argument("--no-milvus", action="store_true", help="跳过 Milvus 索引确认")
    args = parser.parse_args()

    with Path(args.qa).open("r", encoding="utf-8-sig", newline="") as file:
        rows = [row for row in csv.DictReader(file) if (row.get("question") or "").strip()]
    if not rows:
        print("QA 文件为空")
        return 1

    questions = [(row.get("question") or "").strip() for row in rows]
    duplicates = [q for q, count in Counter(questions).items() if count > 1]

    corpus = load_corpus()
    failures = []
    print(f"QA 条数：{len(rows)}；文档数：{len(corpus)}\n")
    print(f"{'#':>3} {'命中':<26} {'比例':>6}  问题")
    print("-" * 110)
    for index, row in enumerate(rows, 1):
        question = (row.get("question") or "").strip()
        ground_truth = (row.get("ground_truth") or "").strip()
        if not ground_truth:
            failures.append((index, question, "ground_truth 为空"))
            continue
        source, hits, total = best_match(ground_truth, corpus, args.window)
        ratio = hits / total if total else 0.0
        ok = ratio >= args.min_ratio
        if ok and not args.no_milvus:
            source_file = source.split("/")[-1]
            if not milvus_has_source(source_file):
                ok = False
                failures.append((index, question, f"{source} 未在 Milvus 中"))
        if not ok:
            if not any(f[0] == index for f in failures):
                failures.append((index, question, f"命中比例 {ratio:.0%} 低于阈值"))
        print(f"{index:>3} {(source or '<未命中>'):<26} {ratio:>6.0%}  {question[:46]}")

    print("-" * 110)
    if duplicates:
        print(f"重复问题 {len(duplicates)} 条：")
        for question in duplicates:
            print(f"  - {question}")
    if failures:
        print(f"\n未通过 {len(failures)} 条：")
        for index, question, reason in failures:
            print(f"  [{index}] {question[:50]} -> {reason}")
        return 1
    print(f"\n全部 {len(rows)} 条均在文档中找到依据，且已进入 Milvus。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
