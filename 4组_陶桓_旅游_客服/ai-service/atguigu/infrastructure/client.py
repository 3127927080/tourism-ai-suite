from typing import Any

import httpx

from atguigu.common.config import get_settings


class TourismDataClient:
    """使用当前客户身份访问 Tourism Service 接口。"""

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.settings = get_settings()

    async def get_json(
            self,
            resource_path: str,
            query_params: dict[str, Any] | None = None
    ) -> Any:
        """发送只读请求并返回 JSON 响应数据"""
        # 1. 根据旅游数据服务地址和资源路径构建请求地址
        request_url = (
            f"{self.settings.resolved_tourism_data_base_url.rstrip('/')}/"
            f"{resource_path.lstrip('/')}"
        )
        # 2. 使用当前用户令牌构建认证请求头
        request_headers = {
            "Authorization": f"Bearer {self.access_token}"
        }
        # 3. 发送带超时限制的只读请求
        async with httpx.AsyncClient(
                timeout=self.settings.resolved_tourism_timeout,
                trust_env=False
        ) as http_client:
            response = await http_client.get(
                request_url,
                headers=request_headers,
                params=query_params
            )
            # 4. 检查响应状态并返回 JSON 数据
            response.raise_for_status()
            return response.json()
