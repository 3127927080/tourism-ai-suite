# atguigu/query_process/nodes/node_search_graph.py
"""图检索节点（旅游版）。

匹配策略：槽位优先，正则兜底。
1. 先用槽位实体（`query_entities`）在 Neo4j 精确匹配节点名；
2. 槽位无命中时用正则关键词 + `CONTAINS` 模糊匹配；
3. 仍无命中时用目的地城市兜底。
容错：整路包在超时 + 异常隔离里，失败只丢图路，不影响其余检索路。
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from atguigu.config.config import MilvusConfig, RetrievalConfig
from atguigu.config.relation_schema import RELATION_TYPES
from atguigu.query_process.base import NodeBase
from atguigu.query_process.nodes.node_search_embedding import escape_value
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.milvus_client_tool import get_milvus_client
from atguigu.tool.neo4j_client_tool import get_neo4j_driver

# 通用停用词
STOPWORDS = {"有哪些", "有什么", "推荐", "介绍", "怎么", "如何", "哪里", "什么", "适合", "可以"}


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

        results = self._guarded_search(state, rewritten_query)
        logger.info(f"图检索返回 {len(results)} 条路径")
        return {"graph_results": results}

    # ============================================================
    # 超时与异常隔离
    # ============================================================
    def _guarded_search(self, state: QueryGraphState, rewritten_query: str) -> list:
        """在独立线程里跑图检索，超时或异常都降级为空结果。"""
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="graph_search")
        future = pool.submit(self._search, state, rewritten_query)
        try:
            return future.result(timeout=RetrievalConfig.graph_timeout) or []
        except FuturesTimeout:
            logger.warning(f"图检索超时（>{RetrievalConfig.graph_timeout}s），本路降级为空")
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"图检索失败，本路降级为空：{type(exc).__name__}: {exc}")
            return []
        finally:
            # 不等待线程结束，避免超时把整次查询拖住
            pool.shutdown(wait=False, cancel_futures=True)

    # ============================================================
    # 主流程
    # ============================================================
    def _search(self, state: QueryGraphState, rewritten_query: str) -> list:
        graph_limit = RetrievalConfig.graph_top_n
        driver = get_neo4j_driver()

        with driver.session() as session:
            seeds, strategy = self._find_seeds(session, state, rewritten_query)
            if not seeds:
                logger.warning("未找到匹配实体，图检索返回空")
                return []
            logger.info(f"图检索种子实体（{strategy}）：{seeds}")

            relation_limit_per_entity = max(3, min(8, graph_limit))
            results = []
            seen = set()

            for entity in seeds:
                if len(results) >= graph_limit:
                    break

                rel_records = session.run(
                    """
                    MATCH (e:Entity {name: $entity})-[r:RELATES]-(related:Entity)
                    WHERE r.type IN $relation_types
                    RETURN e.name AS head, r.type AS relation, related.name AS tail
                    LIMIT $limit
                    """,
                    entity=entity,
                    relation_types=RELATION_TYPES,
                    limit=relation_limit_per_entity,
                ).data()

                for record in rel_records:
                    if len(results) >= graph_limit:
                        break
                    key = (record["head"], record["relation"], record["tail"])
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append({
                        "head": record["head"],
                        "relation": record["relation"],
                        "tail": record["tail"],
                        "content": f"{record['head']} 与 {record['tail']} 的关系是：{record['relation']}",
                        "source": "graph",
                        "score": 0.8,
                    })

            return self._attach_provenance(results[:graph_limit])

    # ============================================================
    # 引用溯源：用实体反查 Milvus 切片，补 source_file / city / content_type
    # ============================================================
    def _attach_provenance(self, results: list) -> list:
        """图谱路径本身不带文档出处，这里按头/尾实体反查含该实体的切片补齐。

        只用一次批量 query（不带向量），失败则保留空字段，不阻断图路。
        """
        if not results:
            return results

        names = []
        for item in results:
            for key in ("head", "tail"):
                value = str(item.get(key) or "").strip()
                if value and value not in names:
                    names.append(value)
        if not names:
            return results

        expr = " or ".join(
            f"json_contains(entities, '{escape_value(name)}')" for name in names[:20]
        )
        try:
            client = get_milvus_client()
            rows = client.query(
                collection_name=MilvusConfig.chunks_collection,
                filter=expr,
                output_fields=["source_file", "source_path", "city", "content_type", "entities"],
                limit=200,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"图结果溯源查询失败，引用字段留空：{type(exc).__name__}: {exc}")
            return results

        meta_map = {}
        for row in rows:
            for entity in self._parse_entities(row.get("entities")):
                meta_map.setdefault(entity, row)

        filled = 0
        for item in results:
            for key in ("head", "tail"):
                row = meta_map.get(str(item.get(key) or "").strip())
                if not row:
                    continue
                for field in ("source_file", "source_path", "city", "content_type"):
                    if row.get(field) and not item.get(field):
                        item[field] = row.get(field)
                filled += 1
                break

        logger.info(f"图结果溯源：{filled}/{len(results)} 条补到来源字段")

        # 城市级实体（如「三亚」）不在 entities 里，用 item_name 兜底再补一次
        remaining = [item for item in results if not item.get("source_file")]
        if remaining:
            self._fill_by_city(remaining)
        return results

    def _fill_by_city(self, items: list) -> None:
        """按城市名（item_name）兜底补充来源字段，失败保持原样。"""
        cities = []
        for item in items:
            for key in ("head", "tail"):
                name = str(item.get(key) or "").strip()
                if name and name not in cities:
                    cities.append(name)
        if not cities:
            return
        expr = " or ".join(f"item_name == '{escape_value(c)}'" for c in cities[:10])
        try:
            client = get_milvus_client()
            rows = client.query(
                collection_name=MilvusConfig.chunks_collection,
                filter=expr,
                output_fields=["source_file", "source_path", "city", "content_type", "item_name"],
                limit=50,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"图结果城市兜底查询失败：{type(exc).__name__}: {exc}")
            return

        by_city = {}
        for row in rows:
            by_city.setdefault(str(row.get("item_name") or ""), row)

        filled = 0
        for item in items:
            for key in ("head", "tail"):
                row = by_city.get(str(item.get(key) or "").strip())
                if not row:
                    continue
                for field in ("source_file", "source_path", "city", "content_type"):
                    if row.get(field) and not item.get(field):
                        item[field] = row.get(field)
                filled += 1
                break
        logger.info(f"图结果城市兜底：{filled}/{len(items)} 条补到来源字段")

    @staticmethod
    def _parse_entities(raw) -> list:
        """entities 可能是 JSON 字符串或列表，统一成字符串列表。"""
        if raw is None:
            return []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return []
        if isinstance(raw, list):
            return [str(item) for item in raw if isinstance(item, (str, int, float))]
        return []

    # ============================================================
    # 种子实体查找：槽位 → 正则 → 城市
    # ============================================================
    def _find_seeds(self, session, state: QueryGraphState, rewritten_query: str) -> tuple[list, str]:
        # 1. 槽位实体精确匹配（最可靠）
        seeds = []
        for name in state.get("query_entities") or []:
            name = str(name or "").strip()
            if not name:
                continue
            rows = session.run(
                "MATCH (e:Entity {name: $name}) RETURN e.name AS name LIMIT 1",
                name=name,
            ).data()
            if rows:
                seeds.append(rows[0]["name"])
        if seeds:
            return list(dict.fromkeys(seeds)), "slot"

        # 2. 正则关键词 + CONTAINS 兜底
        keywords = self._extract_keywords(rewritten_query)
        if keywords:
            rows = session.run(
                """
                MATCH (e:Entity)
                WHERE ANY(kw IN $keywords WHERE e.name CONTAINS kw)
                RETURN e.name AS name
                LIMIT 10
                """,
                keywords=keywords,
            ).data()
            seeds = [row["name"] for row in rows]
            if seeds:
                return list(dict.fromkeys(seeds)), "regex"

        # 3. 目的地城市兜底（只精确匹配城市节点，不做模糊展开，避免泛匹配）
        for city in state.get("item_names") or []:
            city = str(city or "").strip()
            if not city:
                continue
            rows = session.run(
                "MATCH (e:Entity {name: $name}) RETURN e.name AS name LIMIT 1",
                name=city,
            ).data()
            if rows:
                seeds.append(rows[0]["name"])
        return list(dict.fromkeys(seeds)), "city"

    @staticmethod
    def _extract_keywords(query: str) -> list:
        """提取查询中的候选实体关键词。"""
        keywords = []
        # 策略1：提取所有连续中文（长度 >= 2）
        keywords.extend(re.findall(r'[一-龥]{2,}', query))
        # 策略2：提取疑问词前面的实体
        match = re.search(r'([一-龥]{2,})\s*(?:有哪些|有什么|推荐|介绍)', query)
        if match:
            keywords.append(match.group(1))
        # 策略3：提取字母数字组合（航班号、线路编号等）
        keywords.extend(re.findall(r'[A-Za-z0-9]+', query))
        # 去重，过滤停用词和过短词
        return [kw for kw in set(keywords) if kw not in STOPWORDS and len(kw) >= 2]


if __name__ == "__main__":
    node = NodeSearchGraph()
    init_state = {
        "rewritten_query": "昆大丽 6 日经典游途经哪些城市",
        "item_names": ["云南"],
        "query_entities": ["昆大丽 6 日经典游"],
    }
    result = node.process(init_state)
    logger.info(json_format(result))
