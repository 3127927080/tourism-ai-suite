"""
项目的配置项获取
"""
import os
from dotenv import load_dotenv

env_path=os.path.join(os.path.dirname(__file__),"..","..",".env")
load_dotenv(dotenv_path=env_path,override= True)

class MineruConfig:
    mineru_token=os.getenv("MINERU_TOKEN")


class LLMConfig:
    aliyun_api_key = os.getenv("ALIYUN_API_KEY")
    aliyun_api_base = os.getenv("ALIYUN_API_BASE")
    llm_default_model = os.getenv("LLM_DEFAULT_MODEL")
    llm_default_temperature = float(os.getenv("LLM_DEFAULT_TEMPERATURE"))
    vl_model = os.getenv("VL_MODEL")
    item_model = os.getenv("ITEM_MODEL")

class MinIoConfig:
    minio_endpoint = os.getenv("MINIO_ENDPOINT")
    minio_access_key = os.getenv("MINIO_ACCESS_KEY")
    minio_secret_key = os.getenv("MINIO_SECRET_KEY")
    minio_bucket_name = os.getenv("MINIO_BUCKET_NAME")
    minio_img_dir = os.getenv("MINIO_IMG_DIR")

class EmbeddingConfig:
    bge_m3_path=os.getenv("BGE_M3_PATH")
    bge_m3=os.getenv("BGE_M3")
    bge_device=os.getenv("BGE_DEVICE")
    # 特殊处理：将.env中的1/0转为布尔值，兼容常见的数字/字符串格式
    bge_fp16=True if os.getenv("BGE_FP16") in ("1", "True", "true", 1) else False

class MilvusConfig:
    milvus_url=os.getenv("MILVUS_URL")
    chunks_collection=os.getenv("CHUNKS_COLLECTION")
    item_name_collection=os.getenv("ITEM_NAME_COLLECTION")

class MongoConfig:
    # MongoDB 连接 URL
    mongo_url = os.getenv("MONGO_URL")
    # 数据库名
    mongo_db_name = os.getenv("MONGO_DB_NAME")

class McpConfig:
    mcp_base_url=os.getenv("MCP_DASHSCOPE_BASE_URL")
    # MCP 网络搜索使用百炼服务，独立配置 key，避免与 LLM 供应商绑定。
    api_key=os.getenv("MCP_API_KEY") or os.getenv("ALIYUN_API_KEY")

class Neo4jConfig:
    """Neo4j 数据库配置类"""
    URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
    PASSWORD = os.getenv("NEO4J_PASSWORD", "")
    DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

    @classmethod
    def get_auth(cls):
        """返回认证元组，供驱动使用"""
        return (cls.USERNAME, cls.PASSWORD)


class RerankConfig:
    rerank_base_url = os.getenv("RERANK_BASE_URL")
    rerank_api_key = os.getenv("RERANK_API_KEY")
    rerank_model = os.getenv("RERANK_MODEL", "qwen3-rerank")


class InternalAuthConfig:
    """客服系统调用知识检索接口的内部令牌，不复用其他服务令牌。"""

    knowledge_service_token = os.getenv("KNOWLEDGE_SERVICE_TOKEN", "")
