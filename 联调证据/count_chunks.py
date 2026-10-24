"""只读：统计 Milvus 中各 source_file 的切片数。"""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "4组_陶桓_旅游_知识库"
sys.path.insert(0, str(ROOT))

from atguigu.config.config import MilvusConfig  # noqa: E402
from atguigu.tool.milvus_client_tool import get_milvus_client  # noqa: E402

client = get_milvus_client()
rows = client.query(
    collection_name=MilvusConfig.chunks_collection,
    filter="",
    output_fields=["source_file"],
    limit=5000,
)
counter = Counter((r.get("source_file") or "<empty>") for r in rows)
print(f"总切片: {len(rows)}")
for name, count in counter.most_common():
    print(f"{count:5d}  {name}")
