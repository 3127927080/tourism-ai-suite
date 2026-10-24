"""只读确认测试文档是否已写入 Milvus 与 Neo4j。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "4组_陶桓_旅游_知识库"
sys.path.insert(0, str(ROOT))

from atguigu.config.config import MilvusConfig, Neo4jConfig  # noqa: E402
from atguigu.tool.milvus_client_tool import get_milvus_client  # noqa: E402
from atguigu.tool.neo4j_client_tool import get_neo4j_driver  # noqa: E402

client = get_milvus_client()
chunks = client.query(
    collection_name=MilvusConfig.chunks_collection,
    filter='source_file like "%联调测试%"',
    output_fields=["source_file", "content_type", "city", "content"],
    limit=50,
)
print(f"Milvus 命中 {len(chunks)} 条")
for row in chunks[:3]:
    print(" -", row.get("source_file"), "|", row.get("content_type"), "|", row.get("city"),
          "|", (row.get("content") or "")[:40].replace("\n", " "))

driver = get_neo4j_driver()
with driver.session() as session:
    total = session.run("MATCH ()-[r:RELATES]->() RETURN count(r) AS c").single()["c"]
    rel = session.run(
        """
        MATCH (h:Entity)-[r:RELATES]->(t:Entity)
        WHERE h.name CONTAINS '西湖' OR t.name CONTAINS '西湖'
        RETURN h.name AS h, r.type AS rel, t.name AS t LIMIT 10
        """
    ).data()
print(f"Neo4j 关系总数: {total}")
for row in rel:
    print(" -", row["h"], "-[", row["rel"], "]->", row["t"])
