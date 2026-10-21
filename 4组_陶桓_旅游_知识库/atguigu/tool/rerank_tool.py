import dashscope
from http import HTTPStatus

from atguigu.config.config import RerankConfig
from atguigu.tool.logger import logger

# 以下为华北2（北京）地域的配置，调用时请将{WorkspaceId}替换为真实的业务空间ID，各地域的配置不同。
dashscope.base_http_api_url = RerankConfig.rerank_base_url
dashscope.api_key = RerankConfig.rerank_api_key
def text_rerank(query,texts,limit=10):
    try:
        resp = dashscope.TextReRank.call(
            model=RerankConfig.rerank_model,
            query=query,
            documents=texts,
            top_n=limit,
            return_documents=True,
            instruct="Given a web search query, retrieve relevant passages that answer the query."
        )
        if resp.status_code == HTTPStatus.OK:
            return [
                {
                    "index": item.index,
                    "score": item.relevance_score
                }
                for item in resp.output.results
            ]
        else:
            # 保留服务端返回的 code 和 message，便于定位鉴权、配额或模型问题。
            raise Exception(f"Error: {resp.status_code}-{resp.code}-{resp.message}")
    except Exception as e:
        logger.error(f"Error: {e}")
        raise e