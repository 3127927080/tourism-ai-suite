# atguigu/query_process/nodes/node_search_embedding_hyde.py
import json

from langchain.chat_models import init_chat_model
from openai import api_key
from torch.distributed.debug._frontend import format_json

from atguigu.config.config import LLMConfig, MilvusConfig
from atguigu.config.prompt import HYDE_PROMPT
from atguigu.query_process.base import NodeBase
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
        item_names = state.get("item_names", [])
        if not item_names:
            logger.error("请输入要检索的主体名")
            raise Exception("请输入要检索的主体名")

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
            api_key=LLMConfig.aliyun_api_key,
            base_url=LLMConfig.aliyun_api_base
        )
        messages = [
            {"role": "user", "content": HYDE_PROMPT.format(rewritten_query=rewritten_query)}
        ]
        res = llm.invoke(input=messages)
        hyde_answer = res.content
        return hyde_answer

    def get_hyde_embedding_chunks(self, hyde_answer, item_names,rewritten_query):
        merged_query = rewritten_query + " " + hyde_answer
        rewritten_query_embeddings = get_embeddings([merged_query])
        # 准备查询向量
        collection_name = MilvusConfig.chunks_collection
        # item_names = [
        #     item_name.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
        #     for item_name in item_names
        # ]
        expr = f"item_name in {json.dumps(item_names, ensure_ascii=False)}"

        reqs = create_reqs(
            dense_data=rewritten_query_embeddings.get("dense", []),
            sparse_data=rewritten_query_embeddings.get("sparse", []),
            dense_anns_field="dense_vector",
            sparse_anns_field="sparse_vector",
            limit=10,
            dense_param={"metric_type": "COSINE"},
            sparse_param={"metric_type": "IP"},
            expr=expr
        )

        res = search_hybird(
            collection_name=collection_name,
            reqs=reqs,
            ranker=(0.8, 0.2),
            output_fields=["id", "file_title", "title", "content", "item_name"]
        )
        print(json_format(res))
        logger.info(json_format(res[0]))
        # 对结果进行处理
        hyde_embedding_chunks = [
            {
                **item.get("entity"),
                "score": item.get("distance"),
                "source": "local"
            }
            for item in res[0]
        ]
        return hyde_embedding_chunks

    def process(self, state: QueryGraphState):
        """
        节点功能：HyDE (Hypothetical Document Embedding)
        先让 LLM 生成假设性答案，再对答案进行向量检索，提高召回率
        :param state: 工作流状态对象
        :return: 更新后的状态对象
        """
        logger.info(f"【{self.name}】节点逻辑")
        # 第一步,获取上一步重写的问题
        item_names, rewritten_query = self.get_rewitten_query(state)

        #2,第二步,调用大模型生成假设性答案
        hyde_answer = self.get_hyde_answer(rewritten_query)

        #3,讲生成的答案和问题组合,然后向量化去查表
        hyde_embedding_chunks = self.get_hyde_embedding_chunks(hyde_answer, item_names, rewritten_query)

        # return state
        return {"hyde_embedding_chunks": hyde_embedding_chunks}


if __name__ == "__main__":
    init_state = {
        "rewritten_query": "关于兄弟HAK180烫金机如何使用",
        "item_names": ["兄弟HAK180烫金机"]
    }
    node_search_embedding_hyde = NodeSearchEmbeddingHyde()
    result = node_search_embedding_hyde(init_state)
    logger.info(json_format(result))