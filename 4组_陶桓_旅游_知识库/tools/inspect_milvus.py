"""只读检查 Milvus 中的集合与数据，用于确认历史入库是否留存。

用法：
    uv run python tools/inspect_milvus.py
    uv run python tools/inspect_milvus.py --sample 30 --limit 20000

不会写入、不会删除任何数据。
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from atguigu.config.config import MilvusConfig  # noqa: E402
from atguigu.tool.milvus_client_tool import get_milvus_client  # noqa: E402

VECTOR_TYPES = {"FLOAT_VECTOR", "SPARSE_FLOAT_VECTOR", "BINARY_VECTOR", "FLOAT16_VECTOR"}


def field_names(desc: dict) -> list[str]:
    """兼容不同 pymilvus 版本的 describe_collection 返回结构。"""
    fields = desc.get("fields") or []
    return [f.get("name") for f in fields if f.get("name")]


def scalar_fields(desc: dict) -> list[str]:
    """只取标量字段，向量字段不参与投影。"""
    out = []
    for f in desc.get("fields") or []:
        name = f.get("name")
        if not name or name in ("dense_vector", "sparse_vector"):
            continue
        ftype = str(f.get("type") or f.get("datatype") or "")
        if "VECTOR" in ftype.upper():
            continue
        out.append(name)
    return out


def safe_stats(client, name: str) -> int | None:
    """优先用 count(*) 聚合，比 get_collection_stats 准确（后者在部分版本不刷新）。"""
    try:
        rows = client.query(
            collection_name=name, filter="", output_fields=["count(*)"]
        )
        if rows:
            return int(list(rows[0].values())[0])
    except Exception:  # noqa: BLE001
        pass
    try:
        return int(client.get_collection_stats(collection_name=name).get("row_count", 0))
    except Exception as exc:  # noqa: BLE001
        print(f"  读取行数失败：{exc}")
        return None


def top_counter(rows: list[dict], key: str, topn: int = 15) -> None:
    values = [str(r.get(key, "") or "").strip() or "<空>" for r in rows]
    counter = Counter(values)
    if not counter:
        return
    print(f"  {key} 分布（样本 {len(values)} 条）：")
    for value, count in counter.most_common(topn):
        print(f"    {value:<28} {count}")


def inspect_collection(client, name: str, sample: int, limit: int) -> None:
    print(f"\n{'=' * 70}\n集合：{name}")
    desc = client.describe_collection(collection_name=name)
    print(f"  字段：{', '.join(field_names(desc))}")

    total = safe_stats(client, name)
    if total is not None:
        print(f"  总行数：{total}")
        if total == 0:
            return

    fields = scalar_fields(desc)
    if "content" in fields:
        fields.remove("content")  # 正文太长，默认不打印

    try:
        rows = client.query(
            collection_name=name,
            filter="",
            output_fields=fields,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  查询失败：{exc}")
        return

    print(f"  实际取回：{len(rows)} 条" + ("（已达 limit，统计为下限）" if len(rows) >= limit else ""))

    for key in ("file_title", "item_name", "content_type", "city", "source_path", "parent_item_name"):
        if key in fields:
            top_counter(rows, key)

    if sample > 0 and rows:
        print(f"  样例 {min(sample, len(rows))} 条：")
        for row in rows[:sample]:
            brief = {k: row.get(k) for k in fields if k != "id"}
            print(f"    {brief}")


def main() -> int:
    parser = argparse.ArgumentParser(description="只读检查 Milvus 集合与历史数据")
    parser.add_argument("--sample", type=int, default=5, help="每集合打印的样例条数，0 表示不打印")
    parser.add_argument("--limit", type=int, default=16000, help="单集合最多取回多少条做分布统计（Milvus 上限 16384）")
    args = parser.parse_args()
    # Milvus 查询窗口上限是 16384，超出会直接报错
    args.limit = max(1, min(args.limit, 16384))

    print(f"Milvus：{MilvusConfig.milvus_url}")
    print(f"配置集合：CHUNKS_COLLECTION={MilvusConfig.chunks_collection}，"
          f"ITEM_NAME_COLLECTION={MilvusConfig.item_name_collection}")

    client = get_milvus_client()
    collections = sorted(client.list_collections())
    print(f"\n全部集合（{len(collections)} 个）：")
    for name in collections:
        print(f"  - {name}")

    for name in collections:
        try:
            inspect_collection(client, name, args.sample, args.limit)
        except Exception as exc:  # noqa: BLE001
            print(f"\n集合 {name} 检查失败：{exc}")

    print("\n完成。以上为只读结果。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
