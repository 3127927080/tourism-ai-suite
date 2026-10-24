# atguigu/import_process/nodes/node_bge_embedding.py
import json

from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.embedding_utils import get_embeddings
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger


class NodeBGEEmbedding(NodeBase):
    """
    混合向量化节点：使用 BGE-M3 模型将文本转换为向量
    """

    name = "node_bge_embedding"

    def process(self, state: ImportGraphState):
        #1.第一步获取chunks
        chunks = state.get("chunks", [])
        if not chunks:
            logger.error("chunks必须提供")
            raise ValueError("chunks必须提供")

        #2.第二步,讲chunks的content和item_name拼接后进行向量化
        for i in range(0, len(chunks),3):
            chunk_list = chunks[i:i+3]
            #拼接
            content_str_list = [f"{chunk.get("item_name")}{chunk.get("content")}"for chunk in chunk_list]
            #进行向量化
            embeddings = get_embeddings(content_str_list)
            for idx, chunk in enumerate(chunk_list):
                chunk["dense_vector"] = embeddings["dense"][idx]
                chunk["sparse_vector"] = embeddings["sparse"][idx]

        #还是和上个节点一样,保存
        # with open(r"C:\Users\th\Desktop\4组_陶桓_实战\4组_陶桓_旅游_知识库\output\chunks3.json", "w", encoding="utf-8") as f:
        #     f.write(json_format(chunks))

        return {
            "chunks": chunks
        }


if __name__ == '__main__':
    node = NodeBGEEmbedding()
    with open(r"C:\Users\th\Desktop\4组_陶桓_实战\4组_陶桓_旅游_知识库\output\chunks2.json", "r", encoding="utf-8") as f:
        chunks = json.load(f)
    state = {
        "chunks":chunks
    }
    res = node(state)
    logger.info(json_format(res))