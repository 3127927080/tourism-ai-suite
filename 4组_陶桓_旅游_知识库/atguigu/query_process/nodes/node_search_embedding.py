# atguigu/query_process/nodes/node_search_embedding.py
"""向量检索节点（旅游版）。

改造要点：
1. item_names 为空不再抛异常，去掉 expr 做全局检索；
2. 支持 item_name / content_type / entities 组合过滤；
3. content_type 或实体过滤无结果时自动回退，避免过度收窄导致零召回。
"""
import json

from atguigu.config.config import MilvusConfig, RetrievalConfig
from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.embedding_utils import get_embeddings
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.milvus_client_tool import create_reqs, search_hybird

# 检索结果需要带回的元数据字段，供重排与 8005 引用使用
OUTPUT_FIELDS = [
    "id",
    "file_title",
    "title",
    "content",
    "item_name",
    "content_type",
    "city",
    "source_file",
    "source_path",
    "entities",
    "route_name",
    "duration_days",
    "budget_ref",
    "estimated",
]


def escape_value(value) -> str:
    """转义 Milvus 过滤表达式中的字符串值。"""
    return str(value).replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')


def build_filter_expr(item_names: list, content_type: str, entities: list) -> str:
    """构造组合过滤表达式；无任何条件时返回空串。"""
    parts = []
    if item_names:
        parts.append(f"item_name in {json.dumps(item_names, ensure_ascii=False)}")
    if content_type:
        parts.append(f"content_type == '{escape_value(content_type)}'")
    if entities:
        # entities 是 JSON 字符串数组，元素本身不带引号，不能用 '"亚龙湾"' 这种写法
        ors = " or ".join(
            f"json_contains(entities, '{escape_value(entity)}')" for entity in entities
        )
        parts.append(f"({ors})")
    return " and ".join(parts)


class NodeSearchEmbedding(NodeBase):
    """
    节点功能：基于已确认目的地与改写后的问题，执行 Milvus 混合检索
    """

    name: str = "node_search_embedding"

    def search(self, item_names: list, content_type: str, entities: list, rewritten_query: str):
        embeddings = get_embeddings([rewritten_query])
        expr = build_filter_expr(item_names, content_type, entities)
        logger.info(f"向量检索 expr={expr or '<不过滤>'}")

        reqs = create_reqs(
            dense_data=embeddings.get("dense", []),
            sparse_data=embeddings.get("sparse", []),
            dense_anns_field="dense_vector",
            sparse_anns_field="sparse_vector",
            limit=10,
            dense_param={"metric_type": "COSINE"},
            sparse_param={"metric_type": "IP"},
            expr=expr or None,
        )
        res = search_hybird(
            collection_name=MilvusConfig.chunks_collection,
            reqs=reqs,
            ranker=(0.8, 0.2),
            output_fields=OUTPUT_FIELDS,
        )
        hits = res[0] if res else []
        return [
            {
                **(item.get("entity") or {}),
                "score": item.get("distance"),
                "source": "local",
            }
            for item in hits
        ]

    def process(self, state: QueryGraphState):
        logger.info(f"【{self.name}】节点逻辑")
        rewritten_query = state.get("rewritten_query", "")
        if not rewritten_query:
            logger.error("请输入要检索的问题")
            raise Exception("请输入要检索的问题")

        item_names = state.get("item_names") or []
        content_type = state.get("query_content_type") or ""
        entities = state.get("query_entities") or []

        chunks = self.search(item_names, content_type, entities, rewritten_query)

        # 回退 1：内容类型过滤过严
        if not chunks and content_type and RetrievalConfig.content_type_fallback:
            logger.info(f"content_type={content_type} 无结果，回退为不过滤内容类型")
            chunks = self.search(item_names, "", entities, rewritten_query)

        # 回退 2：实体过滤过严
        if not chunks and entities:
            logger.info("实体过滤无结果，回退为不加实体条件")
            chunks = self.search(item_names, content_type, [], rewritten_query)

        if not chunks:
            logger.warning("向量检索无结果")

        return {"embedding_chunks": chunks}


if __name__ == "__main__":
    init_state = {
        "rewritten_query": "三亚有哪些必去景点",
        "item_names": ["三亚"],
        "query_content_type": "景点介绍",
        "query_entities": [],
    }
    node_search_embedding = NodeSearchEmbedding()
    result = node_search_embedding(init_state)
    logger.info(json_format({"count": len(result.get("embedding_chunks", []))}))
