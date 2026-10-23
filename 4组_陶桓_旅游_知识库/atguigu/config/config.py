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
    # ============================================================
    # 阿里云（DashScope）凭据：rerank、视觉理解仍走这里
    # ============================================================
    aliyun_api_key = os.getenv("ALIYUN_API_KEY")
    aliyun_api_base = os.getenv("ALIYUN_API_BASE")

    # ============================================================
    # 语言模型凭据：答案生成、槽位识别、元数据解析、三元组抽取、Ragas 判分
    # 可指向其他供应商（如 DeepSeek）；未配置时回退阿里云，行为与改造前一致
    # ============================================================
    chat_api_key = os.getenv("LLM_API_KEY") or aliyun_api_key
    chat_api_base = os.getenv("LLM_API_BASE") or aliyun_api_base

    # 视觉模型凭据：默认跟随阿里云；如需独立供应商再覆盖
    vl_api_key = os.getenv("VL_API_KEY") or aliyun_api_key
    vl_api_base = os.getenv("VL_API_BASE") or aliyun_api_base

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
    # 旅游改造：新集合与旧电商集合（kb_chunks / kb_item_names）并存，旧集合保留做回归
    chunks_collection=os.getenv("CHUNKS_COLLECTION", "chunks_tourism_v1")
    item_name_collection=os.getenv("ITEM_NAME_COLLECTION", "item_name_tourism_v1")


class TourismConfig:
    """旅游业务常量：目的地白名单、内容类型枚举、多城市展开规则。"""

    # 目的地白名单：元数据里出现这些词即视为城市
    destinations = ["云南", "三亚", "厦门", "张家界", "成都", "杭州"]

    # 内容类型枚举，必须与需求说明保持一致
    content_types = ["景点介绍", "线路推荐", "酒店信息", "美食推荐", "交通指南", "文化民俗"]

    # 数据里写作「云南多城市」的文档，统一展开为城市列表
    multi_city_defaults = {"云南": ["昆明", "大理", "丽江"]}

    # 内容类型 -> 数据目录名（用于还原 source_path）
    content_type_to_dir = {
        "景点介绍": "景点攻略",
        "线路推荐": "线路推荐",
        "酒店信息": "酒店信息",
        "美食推荐": "美食推荐",
        "交通指南": "交通指南",
        "文化民俗": "文化民俗",
    }

    # 数据里「美食介绍」与需求枚举「美食推荐」不一致，入库前统一
    content_type_aliases = {"美食介绍": "美食推荐"}

    unknown_content_type = "未知"


class RetrievalConfig:
    """检索路开关与融合名额。

    分层融合：只有 Milvus 出来的结果（稠密+稀疏、HyDE）做 RRF 倒排融合，
    取 RRF 前 rrf_top_n 条；图检索、网络搜索各取固定条数，三者在 Rerank 汇合。
    图/网不进 RRF，避免为异构结构（图谱路径、网页片段）构造伪排名。
    """

    enable_hyde = os.getenv("ENABLE_HYDE", "false").lower() == "true"
    enable_graph = os.getenv("ENABLE_GRAPH", "false").lower() == "true"
    enable_web_search = os.getenv("ENABLE_WEB_SEARCH", "false").lower() == "true"

    # content_type 过滤无结果时，是否自动回退为不过滤
    content_type_fallback = True

    # 融合名额（可按 Ragas 结果下调，如图/网各 5 条）
    rrf_top_n = int(os.getenv("RRF_TOP_N", "10"))
    graph_top_n = int(os.getenv("GRAPH_TOP_N", "10"))
    web_top_n = int(os.getenv("WEB_TOP_N", "10"))

    # 单路超时（秒）：超时只丢该路，不影响其余检索路
    graph_timeout = float(os.getenv("GRAPH_TIMEOUT", "5"))
    web_timeout = float(os.getenv("WEB_TIMEOUT", "8"))


class ImportConfig:
    """入库链路配置。"""

    # 上传文件落盘根目录，不再硬编码 D:\output
    upload_root = os.getenv("UPLOAD_ROOT") or os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "output")
    )

    # 是否在本期重建图谱；关闭时不跑三元组抽取与 Neo4j 写入
    enable_graph_extract = os.getenv("ENABLE_GRAPH_EXTRACT", "false").lower() == "true"

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
