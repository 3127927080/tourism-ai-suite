import json
from collections.abc import Callable
from typing import Any

import httpx

from atguigu.common.config import Settings, get_settings


class AIServiceGateway:
    """调用真实 AI Service；start_run 支持 NDJSON 流式增量。"""

    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings or get_settings()
        # 可选传输层（便于测试注入 MockTransport）
        self.transport = transport

    async def start_run(
        self,
        token: str,
        request: dict[str, Any],
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """启动 AI Run：消费 NDJSON，逐条回调增量，返回最终事件。"""
        headers = self._build_headers(token)
        url = f"{self.settings.ai_service_url}/internal/v1/agent/runs"

        async with (
            httpx.AsyncClient(
                timeout=self._build_timeout(),
                trust_env=False,
                transport=self.transport,
            ) as client,
            client.stream("POST", url, headers=headers, json=request) as response,
        ):
            response.raise_for_status()

            # 非流式后备：AI 返回普通 JSON 时直接解析
            content_type = response.headers.get("content-type", "")
            if "application/x-ndjson" not in content_type:
                body = await response.aread()
                event = json.loads(body.decode("utf-8"))
                if not isinstance(event, dict):
                    raise TypeError("AI Service 返回的事件格式错误")
                return event

            # NDJSON：run_delta 回调增量，其余为最终事件
            final_event: dict[str, Any] | None = None
            async for line in response.aiter_lines():
                stripped = line.strip()
                if not stripped:
                    continue
                event = json.loads(stripped)
                if event.get("event_type") == "run_delta":
                    if on_delta is not None:
                        delta = event.get("event_data", {}).get("delta", "")
                        if delta:
                            on_delta(str(delta))
                else:
                    final_event = event

            if final_event is None:
                raise RuntimeError("AI Service 未返回最终事件")
            return final_event

    async def commit_run(
        self,
        token: str,
        run_id: str,
        input_revision: int,
    ) -> dict[str, Any]:
        """提交快照校验通过的业务决策。"""
        return await self._post_json(
            f"/internal/v1/agent/runs/{run_id}/confirm",
            token,
            {"input_revision": input_revision},
        )

    async def cancel_run(
        self,
        token: str,
        run_id: str,
    ) -> None:
        """取消快照已经过期或处理失败的 AI Run。"""
        await self._post(
            f"/internal/v1/agent/runs/{run_id}/cancel",
            token,
        )

    def _build_headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "X-Internal-Service-Token": self.settings.internal_service_token,
            "Accept": "application/x-ndjson",
        }

    def _build_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.settings.ai_timeout_seconds, connect=5)

    async def _post_json(
        self,
        path: str,
        token: str,
        request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._post(path, token, request)
        event = response.json()
        if not isinstance(event, dict):
            raise TypeError("AI Service 返回的事件格式错误")
        return event

    async def _post(
        self,
        path: str,
        token: str,
        request: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """发送 AI Service JSON 请求。"""
        headers = self._build_headers(token)
        headers["Accept"] = "application/json"

        async with httpx.AsyncClient(
            timeout=self._build_timeout(),
            trust_env=False,
            transport=self.transport,
        ) as client:
            response = await client.post(
                f"{self.settings.ai_service_url}{path}",
                headers=headers,
                json=request,
            )
            response.raise_for_status()
            return response
