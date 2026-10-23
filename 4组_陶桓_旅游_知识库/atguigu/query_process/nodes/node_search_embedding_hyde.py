# atguigu/query_process/nodes/node_search_embedding_hyde.py
import json

from langchain.chat_models import init_chat_model

from atguigu.config.config import LLMConfig, MilvusConfig, RetrievalConfig
from atguigu.config.prompt import HYDE_PROMPT
from atguigu.query_process.base import NodeBase
from atguigu.query_process.nodes.node_search_embedding import (
    OUTPUT_FIELDS,
    build_filter_expr,
    effective_item_names,
)
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.embedding_utils import get_embeddings
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.milvus_client_tool import search_hybird, create_reqs


class NodeSearchEmbeddingHyde(NodeBase):
    """
    节点功能：HyDE (Hypothetical Document Embedding)
    先让 LLM 生成假设性答案，再对答案进行向量检索，提高召回率。
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_search_embedding_hyde"

    def get_rewitten_query(self, state: QueryGraphState):
        # 旅游版：目的地识别失败时也允许走全局检索，因此不再强制要求 item_names
        item_names = state.get("item_names", []) or []
        rewritten_query = state.get("rewritten_query", "")
        if not rewritten_query:
            logger.error("请输入要检索的问题")
            raise Exception("请输入要检索的问题")
        return item_names, rewritten_query

    def get_hyde_answer(self, rewritten_query: list[str]):
        llm = init_chat_model(
            model=LLMConfig.item_model,
            model_provider="openai",
            temperature=LLMConfig.llm_default_temperature,
            api_key=LLMConfig.chat_api_key,
            base_url=LLMConfig.chat_api_base
        )
        messages = [
            {"role": "user", "content": HYDE_PROMPT.format(rewritten_query=rewritten_query)}
        ]
        res = llm.invoke(input=messages)
        hyde_answer = res.content
        return hyde_answer

    def get_hyde_embedding_chunks(self, hyde_answer, item_names, rewritten_query, content_type="",
                                  entities=None):
        merged_query = rewritten_query + " " + hyde_answer
        rewritten_query_embeddings = get_embeddings([merged_query])
        collection_name = MilvusConfig.chunks_collection
        expr = build_filter_expr(item_names, content_type, entities or [])

        reqs = create_reqs(
            dense_data=rewritten_query_embeddings.get("dense", []),
            sparse_data=rewritten_query_embeddings.get("sparse", []),
            dense_anns_field="dense_vector",
            sparse_anns_field="sparse_vector",
            limit=10,
            dense_param={"metric_type": "COSINE"},
            sparse_param={"metric_type": "IP"},
            expr=expr or None,
        )

        res = search_hybird(
            collection_name=collection_name,
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
        """
        节点功能：HyDE (Hypothetical Document Embedding)
        先让 LLM 生成假设性答案，再对答案进行向量检索，提高召回率
        :param state: 工作流状态对象
        :return: 更新后的状态对象
        """
        logger.info(f"【{self.name}】节点逻辑")
        # 单路失败只丢 HyDE 这一路，不影响向量检索与其余检索路
        try:
            return self._run(state)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"HyDE 检索失败，本路降级为空：{type(exc).__name__}: {exc}")
            return {"hyde_embedding_chunks": []}

    def _run(self, state: QueryGraphState):
        # 第一步,获取上一步重写的问题
        item_names, rewritten_query = self.get_rewitten_query(state)

        content_type = state.get("query_content_type") or ""
        entities = state.get("query_entities") or []
        # 平台规则类问题不做城市过滤，与向量路保持一致
        item_names = effective_item_names(item_names, content_type)

        # 第二步,调用大模型生成假设性答案
        hyde_answer = self.get_hyde_answer(rewritten_query)

        # 第三步,把生成的答案和问题组合后向量化查表
        chunks = self.get_hyde_embedding_chunks(
            hyde_answer, item_names, rewritten_query,
            content_type=content_type, entities=entities,
        )

        # 与向量路一致的两级回退：内容类型/实体过滤过严时放宽，避免 HyDE 路白跑
        if not chunks and content_type and RetrievalConfig.content_type_fallback:
            logger.info(f"HyDE content_type={content_type} 无结果，回退为不过滤内容类型")
            chunks = self.get_hyde_embedding_chunks(
                hyde_answer, item_names, rewritten_query,
                content_type="", entities=entities,
            )
        if not chunks and entities:
            logger.info("HyDE 实体过滤无结果，回退为不加实体条件")
            chunks = self.get_hyde_embedding_chunks(
                hyde_answer, item_names, rewritten_query,
                content_type=content_type, entities=[],
            )

        return {"hyde_embedding_chunks": chunks}


if __name__ == "__main__":
    init_state = {
        "rewritten_query": "三亚有哪些适合亲子的景点",
        "item_names": ["三亚"],
        "query_content_type": "景点介绍",
        "query_entities": [],
    }
    node_search_embedding_hyde = NodeSearchEmbeddingHyde()
    result = node_search_embedding_hyde(init_state)
    logger.info(json_format({"count": len(result.get("hyde_embedding_chunks", []))}))