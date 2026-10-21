# atguigu/import_process/nodes/node_import_milvus.py
"""向量入库节点（旅游版）。

改造要点：
1. 使用新的 chunks 集合与 schema，带上旅游元数据字段；
2. 写入前显式投影到 schema 字段，丢弃 doc_metadata 等多余键（MilvusClient 对多余字段敏感）；
3. 幂等删除改为按 source_path。
"""
import json

from pymilvus import DataType

from atguigu.config.config import MilvusConfig
from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.milvus_client_tool import get_milvus_client

# Milvus VARCHAR 按字节计，中文约占 3 字节，统一按字节截断
CONTENT_MAX_BYTES = 8000
METADATA_JSON_MAX_BYTES = 8000


def truncate_bytes(text, limit: int) -> str:
    """按 UTF-8 字节数截断字符串，避免破坏多字节字符。"""
    value = "" if text is None else str(text)
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")


def escape_filter_value(value: str) -> str:
    """转义 Milvus 过滤表达式中的字符串值。"""
    return str(value).replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')


class NodeImportMilvus(NodeBase):
    """
    导入向量库节点：数据持久化
    """

    name = "node_import_milvus"

    # ============================================================
    # 一、字段投影
    # ============================================================
    def project_chunks(self, chunks: list) -> list[dict]:
        """把 chunk 投影成 Milvus schema 允许的字段。"""
        rows = []
        for chunk in chunks:
            entities = chunk.get("entities") or []
            if not isinstance(entities, list):
                entities = []
            rows.append(
                {
                    "title": truncate_bytes(chunk.get("title", ""), 200),
                    "file_title": truncate_bytes(chunk.get("file_title", ""), 200),
                    "item_name": truncate_bytes(chunk.get("item_name", ""), 100),
                    "content": truncate_bytes(chunk.get("content", ""), CONTENT_MAX_BYTES),
                    "part": int(chunk.get("part") or 0),
                    "content_type": truncate_bytes(chunk.get("content_type", ""), 32),
                    "city": truncate_bytes(chunk.get("city", ""), 50),
                    "cities": truncate_bytes(chunk.get("cities", "[]"), 512),
                    "scenic_name": truncate_bytes(chunk.get("scenic_name", ""), 200),
                    "route_name": truncate_bytes(chunk.get("route_name", ""), 200),
                    "hotel_name": truncate_bytes(chunk.get("hotel_name", ""), 200),
                    "restaurant_name": truncate_bytes(chunk.get("restaurant_name", ""), 200),
                    "source_file": truncate_bytes(chunk.get("source_file", ""), 200),
                    "source_path": truncate_bytes(chunk.get("source_path", ""), 500),
                    "theme": truncate_bytes(chunk.get("theme", ""), 500),
                    "entities": json.dumps(entities, ensure_ascii=False),
                    "entity_type": truncate_bytes(chunk.get("entity_type", ""), 32),
                    "duration_days": int(chunk.get("duration_days") or 0),
                    "budget_ref": truncate_bytes(chunk.get("budget_ref", ""), 200),
                    "estimated": bool(chunk.get("estimated", False)),
                    "metadata_json": truncate_bytes(
                        chunk.get("metadata_json", ""), METADATA_JSON_MAX_BYTES
                    ),
                    "dense_vector": chunk.get("dense_vector"),
                    "sparse_vector": chunk.get("sparse_vector"),
                }
            )
        return rows

    # ============================================================
    # 二、建表
    # ============================================================
    def create_milvus_collection(self):
        milvus_client = get_milvus_client()
        if not milvus_client:
            logger.error("milvus_client为空,请检查配置")
            raise Exception("milvus_client为空,请检查配置")

        collection_name = MilvusConfig.chunks_collection
        if milvus_client.has_collection(collection_name):
            return collection_name, milvus_client

        schema = milvus_client.create_schema(auto_id=True)
        schema.add_field(
            field_name="id", datatype=DataType.INT64, is_primary=True
        ).add_field(
            field_name="title", datatype=DataType.VARCHAR, max_length=200
        ).add_field(
            field_name="file_title", datatype=DataType.VARCHAR, max_length=200
        ).add_field(
            field_name="item_name", datatype=DataType.VARCHAR, max_length=100
        ).add_field(
            field_name="content", datatype=DataType.VARCHAR, max_length=CONTENT_MAX_BYTES
        ).add_field(
            field_name="part", datatype=DataType.INT64
        ).add_field(
            field_name="content_type", datatype=DataType.VARCHAR, max_length=32
        ).add_field(
            field_name="city", datatype=DataType.VARCHAR, max_length=50
        ).add_field(
            field_name="cities", datatype=DataType.VARCHAR, max_length=512
        ).add_field(
            field_name="scenic_name", datatype=DataType.VARCHAR, max_length=200
        ).add_field(
            field_name="route_name", datatype=DataType.VARCHAR, max_length=200
        ).add_field(
            field_name="hotel_name", datatype=DataType.VARCHAR, max_length=200
        ).add_field(
            field_name="restaurant_name", datatype=DataType.VARCHAR, max_length=200
        ).add_field(
            field_name="source_file", datatype=DataType.VARCHAR, max_length=200
        ).add_field(
            field_name="source_path", datatype=DataType.VARCHAR, max_length=500
        ).add_field(
            field_name="theme", datatype=DataType.VARCHAR, max_length=500
        ).add_field(
            field_name="entities", datatype=DataType.JSON
        ).add_field(
            field_name="entity_type", datatype=DataType.VARCHAR, max_length=32
        ).add_field(
            field_name="duration_days", datatype=DataType.INT64
        ).add_field(
            field_name="budget_ref", datatype=DataType.VARCHAR, max_length=200
        ).add_field(
            field_name="estimated", datatype=DataType.BOOL
        ).add_field(
            field_name="metadata_json",
            datatype=DataType.VARCHAR,
            max_length=METADATA_JSON_MAX_BYTES,
        ).add_field(
            field_name="dense_vector", datatype=DataType.FLOAT_VECTOR, dim=1024
        ).add_field(
            field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR
        )

        index_params = milvus_client.prepare_index_params()
        index_params.add_index(
            field_name="dense_vector",
            index_type="IVF_FLAT",
            metric_type="COSINE",
            params={"nlist": 128, "nprobe": 10},
        )
        index_params.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="IP",
            params={
                "inverted_index_algo": "DAAT_MAXSCORE",
                "normalize": True,
                "quantization": "none",
            },
        )
        # 标量索引：支撑按城市/内容类型过滤与按来源删除
        for field in ("item_name", "content_type", "city", "source_path"):
            index_params.add_index(field_name=field, index_type="INVERTED")

        milvus_client.create_collection(
            collection_name=collection_name,
            schema=schema,
            index_params=index_params,
        )
        logger.info(f"已创建集合：{collection_name}")
        return collection_name, milvus_client

    # ============================================================
    # 三、主流程
    # ============================================================
    def process(self, state: ImportGraphState):
        chunks = state.get("chunks", [])
        if not chunks:
            logger.error("chunks数据为空,无法存库,请检查数据")
            raise Exception("chunks数据为空,无法存库,请检查数据")

        logger.info(f"接收到 chunks 数量: {len(chunks)}")
        rows = self.project_chunks(chunks)

        collection_name, milvus_client = self.create_milvus_collection()
        milvus_client.load_collection(collection_name=collection_name)

        # 幂等删除：同一文档重复上传时先清掉旧切片
        source_path = escape_filter_value(
            rows[0].get("source_path") or rows[0].get("file_title", "")
        )
        milvus_client.delete(
            collection_name=collection_name,
            filter=f"source_path == '{source_path}'",
        )

        res = milvus_client.insert(collection_name=collection_name, data=rows)
        ids = res.get("ids") or []
        for idx, chunk in enumerate(chunks):
            if idx < len(ids):
                chunk["id"] = ids[idx]

        logger.info(f"切片入库完成：{collection_name}，{len(rows)} 条")
        return {
            "chunks": chunks,
        }


if __name__ == "__main__":
    node = NodeImportMilvus()
    with open(r"D:/output/chunks3.json", "r", encoding="utf-8") as f:
        chunks = json.load(f)
    state = {
        "chunks": chunks,
        "file_title": "三亚景点推荐",
    }
    result = node.process(state)
    logger.info(json_format(result))
