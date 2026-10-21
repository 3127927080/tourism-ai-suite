from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.neo4j_client_tool import get_neo4j_driver

import re


# 商品领域关系类型
RELATION_TYPES = [
    "由", "制造", "生产", "出品", "发明", "研发", "属于", "包含",
    "需要", "用于", "提供", "导致", "防止", "指示", "说明"
]

# 通用停用词
STOPWORDS = {"有哪些", "有什么", "推荐", "介绍", "怎么", "如何", "哪里", "什么"}


class NodeSearchGraph(NodeBase):
    """
    节点功能: 从 Neo4j 中检索与查询相关的实体关系路径。
    """

    name: str = "node_search_graph"

    def process(self, state: QueryGraphState):
        logger.info(f"【{self.name}】节点逻辑")

        rewritten_query = state.get("rewritten_query", "")
        if not rewritten_query:
            logger.warning("未提供改写后的问题，跳过图检索")
            return {"graph_results": []}

        # ===== 1. 根据权重计算应取条数 =====
        graph_weight = state.get("graph_weight", 0.5)
        TOTAL_RERANK = 30
        WEB_FIXED = 10
        OTHER_COUNT = TOTAL_RERANK - WEB_FIXED

        graph_limit = max(2, min(18, int(OTHER_COUNT * graph_weight)))
        logger.info(f"graph_weight={graph_weight}, 图检索取 {graph_limit} 条")

        # ===== 2. 使用商品领域关系类型 =====
        relation_types = RELATION_TYPES
        logger.info(f"使用关系类型: {relation_types}")

        # ===== 3. 提取候选关键词 =====
        candidate_keywords = self._extract_keywords(rewritten_query)
        if not candidate_keywords:
            logger.warning("未提取到有效关键词，跳过图检索")
            return {"graph_results": []}

        logger.info(f"候选关键词: {candidate_keywords}")

        # ===== 4. 执行图查询 =====
        driver = get_neo4j_driver()
        results = []
        seen = set()

        with driver.session() as session:
            # 尝试匹配实体
            entity_query = """
            MATCH (e:Entity)
            WHERE ANY(kw IN $keywords WHERE e.name CONTAINS kw)
            RETURN e.name AS entity
            LIMIT 10
            """
            entity_records = session.run(entity_query, keywords=candidate_keywords).data()
            entities = [record["entity"] for record in entity_records]

            # ===== 5. 如果实体匹配失败，尝试降级匹配 =====
            if not entities and len(candidate_keywords) > 1:
                logger.info("首次匹配失败，尝试拆分关键词重新匹配...")
                # 取最短的关键词（通常是核心实体，如 HAK180）
                shortest_kw = min(candidate_keywords, key=len)
                entity_query_short = """
                MATCH (e:Entity)
                WHERE e.name CONTAINS $keyword
                RETURN e.name AS entity
                LIMIT 5
                """
                entity_records_short = session.run(entity_query_short, keyword=shortest_kw).data()
                entities = [record["entity"] for record in entity_records_short]

            if not entities:
                logger.warning("未找到匹配的实体，图检索返回空")
                return {"graph_results": []}

            logger.info(f"找到 {len(entities)} 个候选实体: {entities}")

            # ===== 6. 动态调整每个实体的关系数量 =====
            relation_limit_per_entity = max(3, min(8, int(6 * graph_weight)))

            # ===== 7. 查询关系 =====
            for entity in entities:
                if len(results) >= graph_limit:
                    break

                relation_query = """
                MATCH (e:Entity {name: $entity})-[r:RELATES]-(related:Entity)
                WHERE r.type IN $relation_types
                RETURN e.name AS head, r.type AS relation, related.name AS tail
                LIMIT $limit
                """
                rel_records = session.run(
                    relation_query,
                    entity=entity,
                    relation_types=relation_types,
                    limit=relation_limit_per_entity
                ).data()

                for record in rel_records:
                    if len(results) >= graph_limit:
                        break
                    key = (record["head"], record["relation"], record["tail"])
                    if key not in seen:
                        seen.add(key)
                        results.append({
                            "head": record["head"],
                            "relation": record["relation"],
                            "tail": record["tail"],
                            "content": f"{record['head']} 与 {record['tail']} 的关系是：{record['relation']}",
                            "source": "graph",
                            "score": 0.8
                        })

            logger.info(f"图检索返回 {len(results)} 条路径")
            return {"graph_results": results[:graph_limit]}

    def _extract_keywords(self, query: str) -> list:
        """提取查询中的候选实体关键词。"""
        keywords = []

        # 策略1：提取所有连续中文（长度 >= 2）
        keywords.extend(re.findall(r'[一-龥]{2,}', query))

        # 策略2：提取疑问词前面的实体
        match = re.search(r'([一-龥]{2,})\s*(?:有哪些|有什么|推荐|介绍)', query)
        if match:
            keywords.append(match.group(1))

        # 策略3：提取型号等字母数字组合
        alnum_parts = re.findall(r'[A-Za-z0-9]+', query)
        keywords.extend(alnum_parts)

        # 变体：HAK180 与图谱中的 "HAK 180" 命名保持一致
        for part in alnum_parts:
            if "HAK" in part.upper() and "180" in part:
                keywords.append("HAK 180")

        # 去重，过滤停用词和过短词
        return [kw for kw in set(keywords) if kw not in STOPWORDS and len(kw) >= 2]


if __name__ == "__main__":
    node = NodeSearchGraph()
    init_state = {
        "rewritten_query": "兄弟HAK180烫金机包含哪些部件",
        "graph_weight": 0.5
    }
    result = node(init_state)
    logger.info(json_format(result))
