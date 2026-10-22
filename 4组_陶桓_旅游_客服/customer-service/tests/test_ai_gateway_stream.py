"""AI 网关 NDJSON 消费单元测试（MockTransport，无需真实 AI 服务）。"""

import asyncio
import json
from types import SimpleNamespace

import httpx

from atguigu.worker.ai.gateway import AIServiceGateway


def _settings():
    return SimpleNamespace(
        ai_service_url="http://ai-service",
        internal_service_token="tok",
        ai_timeout_seconds=5,
    )


def test_gateway_streams_deltas():
    lines = [
        {"event_type": "run_delta", "event_data": {"run_id": "r1", "index": 0, "delta": "你好"}},
        {"event_type": "run_delta", "event_data": {"run_id": "r1", "index": 1, "delta": "世界"}},
        {
            "event_type": "run_completed",
            "event_data": {
                "run_id": "r1",
                "message_id": "m1",
                "content": {"text": "你好世界", "reply_type": "ANSWER"},
            },
        },
    ]
    body = "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Internal-Service-Token"] == "tok"
        return httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            text=body,
        )

    gateway = AIServiceGateway(_settings(), transport=httpx.MockTransport(handler))
    received: list[str] = []
    final = asyncio.run(
        gateway.start_run("user-token", {"conversation_id": "c1"}, on_delta=received.append)
    )

    assert received == ["你好", "世界"]
    assert final["event_type"] == "run_completed"
    assert final["event_data"]["content"]["text"] == "你好世界"
    assert final["event_data"]["message_id"] == "m1"


def test_gateway_plain_json_fallback():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "event_type": "run_decision_prepared",
                "event_data": {"run_id": "r2"},
            },
        )

    gateway = AIServiceGateway(_settings(), transport=httpx.MockTransport(handler))
    final = asyncio.run(gateway.start_run("user-token", {"conversation_id": "c1"}))
    assert final["event_type"] == "run_decision_prepared"
    assert final["event_data"]["run_id"] == "r2"


def _run_all():
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    _run_all()
