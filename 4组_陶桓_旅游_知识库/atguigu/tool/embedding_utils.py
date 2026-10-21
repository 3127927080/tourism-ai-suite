from threading import Lock

from pymilvus.model.hybrid import BGEM3EmbeddingFunction

from atguigu.config.config import EmbeddingConfig
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger

bge_embedding_model = None
# 查询流程的多个分支会并行执行，BGE-M3 是共享单例，需要串行访问。
_embedding_lock = Lock()

def get_bge_embedding_model():
    """
    获取全局唯一的一个bge-m3的向量化模型
    :return:
    """
    global bge_embedding_model
    if bge_embedding_model is None:
        with _embedding_lock:
            if bge_embedding_model is None:
                bge_embedding_model = BGEM3EmbeddingFunction(
                    model_name=EmbeddingConfig.bge_m3_path,
                    device=EmbeddingConfig.bge_device,
                    use_fp16=EmbeddingConfig.bge_fp16
                )
    return bge_embedding_model

def get_embeddings(texts):
    """
    获取向量化结果
    :param texts:要向量化的字符串列表
    :return:
    """
    model = get_bge_embedding_model()
    with _embedding_lock:
        embeddings = model(texts)
    return {
        "dense": [d.tolist() for d in embeddings["dense"]],
        "sparse": [dict(zip(row.indices.tolist(), row.data.tolist())) for row in embeddings["sparse"]]
    }

if __name__ == '__main__':
    text_list = ["这是一个测试", "测试向量化结果"]
    embeddings = get_embeddings(text_list)
    logger.info(json_format( embeddings))