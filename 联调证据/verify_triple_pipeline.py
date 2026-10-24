"""验证「微调模型 8006 → 知识库抽取节点 → Neo4j 图谱」链路。

只写入少量测试三元组（MERGE 幂等），不触碰 Milvus 向量库。
用法（知识库 venv）：
    .venv\Scripts\python.exe 联调证据\verify_triple_pipeline.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "4组_陶桓_旅游_知识库"
sys.path.insert(0, str(ROOT))

# 必须在导入节点前设置：NodeGraphExtract.MODE 是类属性，导入时读取
os.environ["TRIPLE_EXTRACTOR_MODE"] = "http"

from atguigu.import_process.nodes.node_graph_extract import NodeGraphExtract  # noqa: E402
from atguigu.import_process.nodes.node_graph_import import NodeGraphImport  # noqa: E402

CHUNKS = [
    {"content": "亚龙湾位于三亚市东南部，以沙滩和海景闻名，适合家庭度假。"},
    {"content": "从三亚市区前往亚龙湾可乘坐公交，沿途途经大东海。"},
    {"content": "蜈支洲岛需要提前预约门票，提供潜水等海上项目。"},
]

result = {
    "extractor_mode": NodeGraphExtract.MODE,
    "service_url": NodeGraphExtract.SERVICE_URL,
    "chunks": len(CHUNKS),
}

extract_node = NodeGraphExtract()
triples = extract_node.process({"chunks": CHUNKS}).get("triples") or []
result["triples"] = triples
result["triple_count"] = len(triples)

if triples:
    stats = NodeGraphImport().process({"triples": triples}).get("graph_import_stats", {})
    result["graph_import_stats"] = stats
else:
    result["graph_import_stats"] = {"total": 0, "success": 0, "fail": 0}

print(json.dumps(result, ensure_ascii=False, indent=2))
