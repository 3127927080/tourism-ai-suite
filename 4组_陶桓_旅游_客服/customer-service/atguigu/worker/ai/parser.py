from typing import Any

from atguigu.app.schemas.event import AgentEventType


class AIEventParser:

    @staticmethod
    def find_run_id(event: dict[str, Any]) -> str:
        run_id = event.get("event_data", {}).get("run_id")

        if run_id:
            return run_id

        raise ValueError(f"AI Service 没有返回run_id")

    @staticmethod
    def has_prepared_decision(event: dict[str, Any]) -> bool:
        return event.get('event_type') == AgentEventType.RUN_DECISION_PREPARED

    @staticmethod
    def parser_outcome(event: dict[str, Any]) -> dict[str, Any]:
        event_type = event["event_type"]
        event_data = event.get("event_data", {})

        if event_type == AgentEventType.RUN_FAILED:
            raise RuntimeError(
                str(event_data.get('message') or "AI Service 处理失败")
            )

        if event_type == AgentEventType.RUN_COMPLETED:
            return {**event_data, "outcome_type": "reply"}

        if event_type == AgentEventType.RUN_HANDOFF_REQUESTED:
            return {
                **event_data,
                "outcome_type": "handoff",
                "summary": str(event_data.get("summary") or "用户请求人工客服"),
                "content": {
                    "text": str(event_data.get("message") or "正在为你转接人工客服，请稍候。")
                },
            }

        raise RuntimeError("AI Service 没有返回最终处理结果")
