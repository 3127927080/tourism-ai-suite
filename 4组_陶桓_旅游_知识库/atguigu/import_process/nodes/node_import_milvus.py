# atguigu/import_process/nodes/node_import_milvus.py
import json

from pymilvus import DataType

from atguigu.config.config import MilvusConfig
from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.milvus_client_tool import get_milvus_client


class NodeImportMilvus(NodeBase):
    """
    导入向量库节点：数据持久化
    """

    name = "node_import_milvus"

    def process(self, state: ImportGraphState):
        #1,第一步导入数据
        chunks = state.get("chunks",[])
        if not chunks:
            logger.error("chunks数据为空,无法存库,请检查数据")
            raise Exception("chunks数据为空,无法存库,请检查数据")


        logger.info(f"接收到 chunks 数量: {len(chunks)}")
        logger.info(f"第一个 chunk 的字段: {chunks[0].keys() if chunks else 'empty'}")
        logger.info(f"file_title: {state.get('file_title')}")
        #2.建表,准备存数据
        milvus_client = get_milvus_client()
        if not milvus_client:
            logger.error("milvus_client为空,请检查配置")
            raise Exception("milvus_client为空,请检查配置")
        collection_name = MilvusConfig.chunks_collection
        if not milvus_client.has_collection(collection_name):
            schema = milvus_client.create_schema(
               auto_id=True,
            )
            schema.add_field(
                field_name="id",
                datatype=DataType.INT64,
                is_primary=True,
            ).add_field(
                field_name="title",
                datatype=DataType.VARCHAR,
                max_length=100
            ).add_field(
                field_name="file_title",
                datatype=DataType.VARCHAR,
                max_length=100
            ).add_field(
                field_name="item_name",
                datatype=DataType.VARCHAR,
                max_length=100
            ).add_field(
                field_name="content",
                datatype=DataType.VARCHAR,
                max_length=5000
            ).add_field(
                field_name="part",
                datatype=DataType.INT64,
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

        #3.准备存入数据
        milvus_client.load_collection(collection_name=collection_name)
        #幂等性删除,对与相同的file_title进行幂等性删除
        file_title = chunks[0].get("file_title")
        #处理file_title里面的特殊字符
        file_title_str =file_title.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
        filter_str = f"file_title == '{file_title_str}'"
        milvus_client.delete(collection_name=collection_name, filter=filter_str)

        #开始插入数据
        res = milvus_client.insert(collection_name=collection_name, data= chunks)
        ids = res.get("ids")
        for idx, chunk in enumerate(chunks):
            chunk["id"] = ids[idx]
        return {
            "chunks": chunks,
        }


if __name__ == "__main__":
    Node = NodeImportMilvus()
    with open(r"D:\output\doc\parse\hak180产品安全手册\chunks3.json", "r",encoding="utf-8") as f:
        chunks = json.load(f)

    state = {
        "chunks": chunks,
        "file_title": "hak180产品安全手册",
    }
    result = Node.process(state)
    logger.info(json_format( result))