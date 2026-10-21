# atguigu/query_process/nodes/node_search_embedding.py
import json

from atguigu.config.config import MilvusConfig
from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.embedding_utils import get_embeddings
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.milvus_client_tool import create_reqs, search_hybird
from atguigu.tool.mongo_client_tool import get_history_list


class NodeSearchEmbedding(NodeBase):
     """
    节点功能：基于已确认主体名+改写后的用户问题，执行Milvus向量数据库混合检索
    """

     # 覆盖基类的 name 属性，标识节点名称
     name: str = "node_search_embedding"

     def get_rewitten_query(self, state: QueryGraphState) -> tuple[str, list[str]]:
         item_names = state.get("item_names", [])
         if not item_names:
             logger.error("请输入要检索的主体名")
             raise Exception("请输入要检索的主体名")

         rewritten_query = state.get("rewritten_query", "")
         if not rewritten_query:
             logger.error("请输入要检索的问题")
             raise Exception("请输入要检索的问题")
         return item_names, rewritten_query

     def get_embedding_chunks(self, item_names: str, rewritten_query: list[str]):
         rewritten_query_embeddings = get_embeddings([rewritten_query])
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
         embedding_chunks = [
             {
                 **item.get("entity"),
                 "score": item.get("distance"),
                 "source": "local"
             }
             for item in res[0]
         ]
         return embedding_chunks

     def process(self, state: QueryGraphState):
         """
         节点功能：基于已确认主体名+改写后的用户问题，执行Milvus向量数据库混合检索
         :param state: 工作流状态对象
         :return: 更新后的状态对象
         """
         logger.info(f"【{self.name}】节点逻辑")
         #第一步,获取上一步重写的问题
         item_names, rewritten_query = self.get_rewitten_query(state)

         #第二步,将上面的问题向量化后查询milvus表
         embedding_chunks = self.get_embedding_chunks(item_names, rewritten_query)

         # return state
         return {"embedding_chunks":  embedding_chunks}




if __name__ == '__main__':
     # 模拟会话历史
     session_id = "test_001"
     # add_or_update_history(session_id, "user", "咨询下烫金机。")
     # add_or_update_history(session_id, "assistant", "您好。请问是哪个型号")
     # add_or_update_history(session_id, "user", "hak180")
     # add_or_update_history(session_id, "assistant", "具体有什么问题呢？")
     # 初始化图状态
     init_state = {
         "rewritten_query": "关于兄弟HAK180烫金机如何使用",
        "item_names": ["兄弟HAK180烫金机"]
     }

     # 创建节点对象
     node_search_embedding = NodeSearchEmbedding()
     # 执行节点的单元测试
     result = node_search_embedding(init_state)
     # 将返回的图状态进行json序列化
     logger.info(json_format(result))