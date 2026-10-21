from pymilvus import MilvusClient, AnnSearchRequest, WeightedRanker

from atguigu.config.config import MilvusConfig

milvus_client = None

def get_milvus_client():
    global milvus_client
    # 1. 如果对象不存在，或者连接已失效，则重建
    if milvus_client is None or not _is_connected(milvus_client):
        if milvus_client is not None:
            try:
                milvus_client.close()  # 尝试释放旧资源
            except Exception:
                pass
        milvus_client = MilvusClient(uri=MilvusConfig.milvus_url)
        return milvus_client
    return milvus_client

def _is_connected(client):
    """健康检查：尝试轻量级操作，探测连接是否存活"""
    try:
        # list_collections 是轻量级操作，不涉及具体数据
        client.list_collections(timeout=3)
        return True
    except Exception:
        return False

def create_reqs(
        dense_data: list,
        sparse_data: list,
        dense_anns_field=None,
        sparse_anns_field=None,
        limit=10,
        dense_param=None,
        sparse_param=None,
        expr=None
):
    """
    创建查找策略对象
    :return:
    """
    if not dense_param:
        dense_param = {
            "metric_type": "COSINE",
        }
    if not sparse_param:
        sparse_param = {
            "metric_type": "IP",
        }
    #稠密的
    dense_req = AnnSearchRequest(
        data=dense_data,
        anns_field=dense_anns_field,
        param=dense_param,
        limit=limit,
        expr=expr
    )
    #稀疏的
    sparse_req = AnnSearchRequest(
        data=sparse_data,
        anns_field=sparse_anns_field,
        param=sparse_param,
        limit=limit,
        expr=expr
    )
    return [dense_req, sparse_req]

def search_hybird(collection_name, reqs,ranker=(0.5,0.5),limit=10,output_fields=None):
    """
    混合搜索
    :param collection_name:要查找的集合名
    :param reqs:一个或多个向量检索请求的列表
    :param ranker:重排序（Reranking）策略
    :param limit:最终返回的结果总数
    :param output_fields:指定结果中需要返回的字段列表
    :return:
    """
    milvus_client = get_milvus_client()
    weight_ranker = WeightedRanker(*ranker,norm_score=True)
    result = milvus_client.hybrid_search(
        collection_name=collection_name,
        reqs=reqs,
        ranker=weight_ranker,
        limit=limit,
        output_fields=output_fields
    )
    return result