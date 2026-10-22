# atguigu/import_process/nodes/node_item_name_recognition.py
"""主体识别节点（旅游版）。

改造要点：
1. item_name 粒度 = 目的地城市，优先取元数据里的 city，缺失才调 LLM；
2. 为城市与文档实体各写一行别名记录，parent_item_name 指向主城市，
   使「大理」「亚龙湾」这类查询能命中主城市文档；
3. 幂等删除改为按 source_path，避免误删同城市其他文档。
"""
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
from atguigu.tool.milvus_client_tool import get_milvus_client


def escape_filter_value(value: str) -> str:
    """转义 Milvus 过滤表达式中的字符串值。"""
    return str(value).replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')


class NodeItemNameRecognition(NodeBase):
    """
    主体识别节点：主体识别与别名写入
    """

    name = "node_item_name_recognition"

    # ============================================================
    # 一、主体名称
    # ============================================================
    def get_chunks(self, state: ImportGraphState) -> list:
        chunks = state.get("chunks", [])
        if not chunks:
            logger.error("chunks必须提供")
            raise ValueError("chunks必须提供")
        return chunks

    def get_chunks_content(self, chunks: list, file_title: str) -> str:
        chunk_k_list = chunks[:10]
        max_len = 10000
        content_str = "\n"
        for idx, chunk in enumerate(chunk_k_list, start=1):
            title = chunk.get("title")
            content = chunk.get("content")
            chunk_str = f"[切片{idx}]\n{file_title}\n{title}\n{content}\n"
            if len(content_str) > max_len:
                logger.info("已经超过最大长度，不再拼接")
                break
            content_str += chunk_str
        return content_str[:max_len]

    def get_item_name_by_llm(self, content_str: str, file_title: str) -> str:
        """元数据缺失时的兜底：让 LLM 识别目的地城市。"""
        if not content_str:
            return ""
        logger.info("元数据缺少城市，调用大模型进行主体识别")
        llm = init_chat_model(
            model=LLMConfig.item_model,
            model_provider="openai",
            api_key=LLMConfig.chat_api_key,
            base_url=LLMConfig.chat_api_base,
            temperature=LLMConfig.llm_default_temperature,
        )
        messages = [
            {"role": "system", "content": ITEM_NAME_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": ITEM_NAME_USER_PROMPT_TEMPLATE.format(
                    file_title=file_title, context=content_str
                ),
            },
        ]
        res = llm.invoke(messages)
        text = str(res.content or "").strip()
        text = text.replace("```json", "").replace("```", "").strip()
        try:
            data = json.loads(text)
            return str(data.get("city") or "").strip()
        except (json.JSONDecodeError, AttributeError):
            # 兜底：模型直接返回了城市名
            return text.replace(" ", "").replace("\n", "")

    def get_item_name(self, state: ImportGraphState, chunks: list) -> str:
        meta = state.get("doc_metadata") or {}
        city = str(meta.get("city") or "").strip()
        if city:
            return city
        file_title = state.get("file_title") or chunks[0].get("file_title") or ""
        content_str = self.get_chunks_content(chunks, file_title)
        item_name = self.get_item_name_by_llm(content_str, file_title)
        if not item_name:
            logger.warning(f"主体识别失败，使用文件名兜底：{file_title}")
            item_name = file_title
        return item_name

    # ============================================================
    # 二、别名行
    # ============================================================
    def build_alias_rows(self, meta: dict, item_name: str, chunks: list) -> list[dict]:
        """主城市一行，cities 与 entities 各写一行别名，去重后返回。"""
        rows: list[dict] = []
        seen: set[str] = set()

        def add(name, parent: str) -> None:
            value = str(name or "").strip()
            if not value or value in seen:
                return
            # Milvus VARCHAR(100)，留出余量
            if len(value) > 90:
                return
            seen.add(value)
            rows.append({"item_name": value, "parent_item_name": parent})

        add(item_name, item_name)
        for city in meta.get("cities") or []:
            add(city, item_name)
        for entity in meta.get("entities") or []:
            if isinstance(entity, dict):
                add(entity.get("name"), item_name)

        source_file = meta.get("source_file") or chunks[0].get("file_title", "")
        source_path = meta.get("source_path") or source_file
        for row in rows:
            row["content_type"] = meta.get("content_type", "")
            row["source_file"] = source_file
            row["source_path"] = source_path
        return rows

    # ============================================================
    # 三、集合与写入
    # ============================================================
    def create_milvus_collection(self) -> tuple[str, object]:
        milvus_client = get_milvus_client()
        if not milvus_client:
            logger.error("初始化milvus_client失败")
            raise Exception("初始化milvus_client失败")

        collection_name = MilvusConfig.item_name_collection
        if not milvus_client.has_collection(collection_name):
            schema = milvus_client.create_schema(auto_id=True)
            schema.add_field(
                field_name="id", datatype=DataType.INT64, is_primary=True
            ).add_field(
                field_name="item_name", datatype=DataType.VARCHAR, max_length=100
            ).add_field(
                field_name="parent_item_name", datatype=DataType.VARCHAR, max_length=100
            ).add_field(
                field_name="content_type", datatype=DataType.VARCHAR, max_length=32
            ).add_field(
                field_name="source_file", datatype=DataType.VARCHAR, max_length=200
            ).add_field(
                field_name="source_path", datatype=DataType.VARCHAR, max_length=500
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
            # 标量索引：便于按来源删除与按内容类型过滤
            for field in ("item_name", "parent_item_name", "content_type", "source_path"):
                index_params.add_index(field_name=field, index_type="INVERTED")

            milvus_client.create_collection(
                collection_name=collection_name,
                schema=schema,
                index_params=index_params,
            )
        return collection_name, milvus_client

    def insert_into_milvus(
        self, chunks: list, collection_name: str, milvus_client, meta: dict, item_name: str
    ) -> None:
        milvus_client.load_collection(collection_name=collection_name)

        # 按 source_path 幂等删除该文档的所有别名行
        source_path = escape_filter_value(
            meta.get("source_path") or chunks[0].get("file_title", "")
        )
        milvus_client.delete(
            collection_name=collection_name,
            filter=f"source_path == '{source_path}'",
        )

        rows = self.build_alias_rows(meta, item_name, chunks)
        if not rows:
            logger.warning("没有可写入的主体别名行")
            return

        embeddings = get_embeddings([row["item_name"] for row in rows])
        data = []
        for idx, row in enumerate(rows):
            data.append(
                {
                    **row,
                    "dense_vector": embeddings.get("dense")[idx],
                    "sparse_vector": embeddings.get("sparse")[idx],
                }
            )
        milvus_client.insert(collection_name=collection_name, data=data)
        logger.info(f"主体别名写入 {len(data)} 行：{', '.join(r['item_name'] for r in rows[:10])}")

        # 回填 item_name 到每个 chunk，供向量化与 chunks 集合使用
        for chunk in chunks:
            chunk["item_name"] = item_name

    # ============================================================
    # 四、主流程
    # ============================================================
    def process(self, state: ImportGraphState):
        chunks = self.get_chunks(state)
        meta = state.get("doc_metadata") or {}
        item_name = self.get_item_name(state, chunks)

        collection_name, milvus_client = self.create_milvus_collection()
        self.insert_into_milvus(chunks, collection_name, milvus_client, meta, item_name)

        return {
            "chunks": chunks,
            "item_name": item_name,
        }


if __name__ == "__main__":
    node = NodeItemNameRecognition()
    md_file = "D:/output/chunks.json"
    with open(md_file, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    state = {
        "chunks": chunks,
        "file_title": "三亚景点推荐",
        "doc_metadata": {
            "content_type": "景点介绍",
            "city": "三亚",
            "cities": ["三亚"],
            "entities": [{"name": "亚龙湾", "type": "景点"}],
            "source_file": "三亚景点推荐.md",
            "source_path": "景点攻略/三亚景点推荐.md",
        },
    }
    res = node(state)
    logger.info(json_format(res))
