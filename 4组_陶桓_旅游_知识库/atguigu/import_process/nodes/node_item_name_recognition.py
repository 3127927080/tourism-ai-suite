# atguigu/import_process/nodes/node_item_name_recognition.py
import json

from langchain.chat_models import init_chat_model
from pymilvus import DataType

from atguigu.config.config import LLMConfig, MilvusConfig
from atguigu.config.prompt import ITEM_NAME_SYSTEM_PROMPT, ITEM_NAME_USER_PROMPT_TEMPLATE
from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.embedding_utils import get_embeddings
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.milvus_client_tool import milvus_client, get_milvus_client


class NodeItemNameRecognition(NodeBase):
    """
    主体识别节点：主体识别与标签提取
    """

    name = "node_item_name_recognition"


    def get_chunks(self, state: ImportGraphState) -> tuple[list, str]:
        chunks = state.get("chunks", [])
        if not chunks:
            logger.error("chunks必须提供")
            raise ValueError("chunks必须提供")

        file_title = state.get("file_title")
        if not file_title:
            logger.error("file_title必须提供")
            raise ValueError("file_title必须提供")
        return chunks, file_title

    def get_chunks_content(self, chunks: list, file_title: str) -> str:
        chunk_k_list = chunks[:10]
        max_len = 10000
        content_str = "\n"
        for idx, chunk in enumerate(chunk_k_list, start=1):
            title = chunk.get("title")
            content = chunk.get("content")
            chunk_str = f"[切片{idx}]\n{file_title}\n{title}\n{content}\n"
            # 判断content_str是不是已经超过max_len
            if len(content_str) > max_len:
                logger.info(f"已经超过最大长度，不再拼接")
                break
            content_str += chunk_str
        content_str = content_str[:max_len]
        return content_str
    def get_item_name(self, content_str: str, file_title: str) -> str:
        # 3.1对chunks进行非空判断
        if not content_str:
            logger.error("content_str必须提供")
            raise ValueError("content_str必须提供")
        logger.info(f"开始调用大模型进行主体识别")
        llm = init_chat_model(
            model=LLMConfig.item_model,
            model_provider="openai",
            api_key=LLMConfig.aliyun_api_key,
            base_url=LLMConfig.aliyun_api_base,
            temperature=LLMConfig.llm_default_temperature
        )
        messages = [
            {"role": "system", "content": ITEM_NAME_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": ITEM_NAME_USER_PROMPT_TEMPLATE.format(file_title=file_title, context=content_str)
            }
        ]

        res = llm.invoke(messages)
        item_name = res.content
        # 对获取的主题进行处理,去除空格
        item_name = item_name.replace(" ", "").replace("\n", "").replace("\t", "")
        # 如果主题为空,则讲file_tilte赋值给item_name
        if not item_name:
            item_name = file_title
        return item_name

    def create_milvus_collection(self) :
        # 获取milvus_client连接
        milvus_client = get_milvus_client()
        if not milvus_client:
            logger.error("初始化milvus_client失败")
            raise Exception("初始化milvus_client失败")
        # 幂等性删除一般不会对整张表进行操作，一般都是针对表里面的相同数据进行幂等删除
        collection_name = MilvusConfig.item_name_collection
        if not milvus_client.has_collection(collection_name):
            schema = milvus_client.create_schema(
                auto_id=True,
            )

            schema.add_field(
                field_name="id",
                datatype=DataType.INT64,
                is_primary=True,
            ).add_field(
                field_name="item_name",
                datatype=DataType.VARCHAR,
                max_length=100,
            ).add_field(
                field_name="file_title",
                datatype=DataType.VARCHAR,
                max_length=100,
            ).add_field(
                field_name="dense_vector",
                datatype=DataType.FLOAT_VECTOR,
                dim=1024
            ).add_field(
                field_name="sparse_vector",
                datatype=DataType.SPARSE_FLOAT_VECTOR,
            )

            index_params = milvus_client.prepare_index_params()
            index_params.add_index(
                field_name="dense_vector",
                index_type="IVF_FLAT",  # 暴力检索
                metric_type="COSINE",
                params={"nlist": 128, "nprobe": 10},  # 提升效率否则暴力检索虽然准备效率太低
            )

            index_params.add_index(
                field_name="sparse_vector",
                index_type="SPARSE_INVERTED_INDEX",  # 暴力检索
                metric_type="IP",
                params={
                    "inverted_index_algo": "DAAT_MAXSCORE",
                    # 高效的稀疏检索算法
                    "normalize": True,
                    # ↑ L2 归一化，让内积 (IP) 等价于余弦相似度
                    "quantization": "none"
                    # ↑ 关闭量化，保持原始精度：模型生成的向量已经压缩的一半的精度了（BGE_FP16=1），这里就不再压缩了
                    # "quantization": "none" → 存储原始向量，不压缩
                    # "quantization": "sq8" → 存储压缩后的向量（8-bit 量化
                }
            )

            milvus_client.create_collection(
                collection_name=collection_name,
                schema=schema,
                index_params=index_params,
            )
        return collection_name, milvus_client

    def insert_into_milvus(self, chunks: list, collection_name: str | None, file_title: str, item_name: str,
                           milvus_client):
        # 对要存入的数据进行幂等性删除
        milvus_client.load_collection(collection_name=collection_name)
        safe_item_name = item_name.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
        filter_str = f"item_name == '{safe_item_name}'"
        milvus_client.delete(collection_name=collection_name, filter=filter_str)
        # 插入数据
        # 通过bgem3去向量化
        embedding = get_embeddings([item_name])
        data = {
            "item_name": item_name,
            "file_title": file_title,
            "dense_vector": embedding.get("dense")[0],
            "sparse_vector": embedding.get("sparse")[0],
        }
        result = milvus_client.insert(
            collection_name=collection_name,
            data=data
        )
        # 回填item_name到每个chunk
        for chunk in chunks:
            chunk["item_name"] = item_name

    def process(self, state: ImportGraphState):
        #第一步,获取上一个节点chunks,
        chunks, file_title = self.get_chunks(state)

        #第二步,将chunks切分,大模型输入长度有限制
        content_str = self.get_chunks_content(chunks, file_title)

        #第三步,调用大模型,进行主体识别
        item_name = self.get_item_name(content_str, file_title)

        #4.第四步,准备存库,先建表
        collection_name, milvus_client = self.create_milvus_collection()

        #5.第五步,将得到的item_name向量化后存表
        self.insert_into_milvus(chunks, collection_name, file_title, item_name, milvus_client)

        #方便下一个节点使用,讲chunks写入文件
        # with open(r"D:\output\doc\parse\hak180产品安全手册\chunks2.json", "w", encoding="utf-8") as f:
        #     f.write(json_format(chunks))


        return {
            "chunks": chunks,
            "item_name": item_name
        }




if __name__ == '__main__':
    node = NodeItemNameRecognition()
    with open(r"D:\output\doc\parse\hak180产品安全手册\chunks.json", "r", encoding="utf-8") as f:
        chunks = json.load(f)
    state = {
        "chunks": chunks,
        "file_title": "hak180产品安全手册"
    }
    res = node(state)
    logger.info(json_format(res))