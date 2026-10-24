"""
图数据持久化节点：将三元组写入 Neo4j
"""
from typing import List, Tuple

from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.neo4j_client_tool import get_neo4j_driver
from atguigu.tool.logger import logger


class NodeGraphImport(NodeBase):
    """
    图数据持久化节点：将提取的三元组 (头实体, 关系, 尾实体) 写入 Neo4j
    """

    name = "node_graph_import"

    def process(self, state: ImportGraphState):
        logger.info(f"【{self.name}】开始执行")

        triples = state.get("triples", [])
        if not triples:
            logger.warning("没有三元组需要写入")
            return {"graph_import_stats": {"total": 0, "success": 0, "fail": 0}}

        logger.info(f"共 {len(triples)} 个三元组待写入")

        driver = get_neo4j_driver()
        success_count = 0
        fail_count = 0

        # 预处理三元组
        valid_triples = []
        for triple in triples:
            if isinstance(triple, list) and len(triple) == 3:
                head, relation, tail = triple
            elif isinstance(triple, dict):
                head = triple.get("head")
                relation = triple.get("relation")
                tail = triple.get("tail")
            else:
                logger.warning(f"跳过无效三元组格式: {triple}")
                fail_count += 1
                continue

            if not head or not relation or not tail:
                logger.warning(f"三元组包含空值，跳过: {triple}")
                fail_count += 1
                continue

            valid_triples.append({"head": head, "relation": relation, "tail": tail})

        if not valid_triples:
            return {
                "graph_import_stats": {
                    "total": len(triples),
                    "success": 0,
                    "fail": fail_count
                }
            }

        try:
            with driver.session() as session:
                cypher = """
                UNWIND $triples AS t
                MERGE (h:Entity {name: t.head})
                MERGE (tail:Entity {name: t.tail})
                MERGE (h)-[r:RELATES {type: t.relation}]->(tail)
                RETURN COUNT(*) AS count
                """
                result = session.run(cypher, triples=valid_triples)
                record = result.single()
                success_count = record["count"] if record else 0
        except Exception as e:
            logger.error(f"批量写入失败: {e}")
            return {
                "graph_import_stats": {
                    "total": len(triples),
                    "success": 0,
                    "fail": len(triples)
                }
            }

        logger.info(f"写入完成: 成功 {success_count} 个，失败 {fail_count} 个")

        return {
            "graph_import_stats": {
                "total": len(triples),
                "success": success_count,
                "fail": fail_count
            }
        }

if __name__ == "__main__":
    node = NodeGraphImport()
    result = node.process({
        "triples": [
            ["亚龙湾", "位于", "三亚"],
            ["昆大丽 6 日经典游", "途经", "大理"],
            ["玉龙雪山", "需要", "提前预约"],
        ]
    })
    print(result.get("graph_import_stats"))