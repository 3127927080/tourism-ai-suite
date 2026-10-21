from typing import Any

import httpx

from atguigu.common.config import get_settings


class KnowledgeClient:
    """使用独立内部令牌访问商品知识检索接口。"""

    def __init__(self):
        self.settings = get_settings()

    async def search(self, query: str, top_k: int) -> dict[str, Any]:
        """检索商品知识，返回原始响应。"""
        # 1. 缺少独立令牌时直接失败，避免把空令牌发给知识库
        token = self.settings.knowledge_service_token
        if not token:
            raise RuntimeError("未配置商品知识服务令牌")

        # 2. 根据服务地址构建检索接口
        request_url = (
            f"{self.settings.knowledge_base_url.rstrip('/')}/knowledge/search"
        )
        request_headers = {
            "X-Internal-Service-Token": token
        }

        # 3. 发送带超时限制的检索请求
        async with httpx.AsyncClient(
                timeout=self.settings.knowledge_timeout_seconds,
                trust_env=False
        ) as http_client:
            response = await http_client.post(
                request_url,
                headers=request_headers,
                json={"query": query, "top_k": top_k}
            )
            response.raise_for_status()
            payload = response.json()

        # 4. 只接受对象响应，避免后续按字段读取时出错
        if not isinstance(payload, dict):
            raise RuntimeError("商品知识服务返回了无法识别的响应")
        return payload
