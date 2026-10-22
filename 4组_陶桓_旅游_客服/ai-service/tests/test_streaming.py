"""NDJSON 流式行构造单元测试（可 pytest 或直接运行）。"""

import json

from atguigu.agent.harness.run.streaming import build_ndjson_lines, chunk_text


def test_chunk_text():
    assert list(chunk_text("abcdefg", 3)) == ["abc", "def", "g"]
    assert list(chunk_text("", 3)) == []


def test_completed_streams_deltas_then_final():
    event = {
        "event_type": "run_completed",
        "event_data": {
            "run_id": "r1",
            "message_id": "m1",
            "content": {"text": "你好世界啊", "reply_type": "ANSWER"},
        },
    }
    parsed = [json.loads(line) for line in build_ndjson_lines(event, 2)]
    deltas = [p for p in parsed if p["event_type"] == "run_delta"]
    assert "".join(d["event_data"]["delta"] for d in deltas) == "你好世界啊"
    assert [d["event_data"]["index"] for d in deltas] == [0, 1, 2]
    assert parsed[-1]["event_type"] == "run_completed"


def test_non_completed_has_no_deltas():
    event = {"event_type": "run_handoff_requested", "event_data": {"run_id": "r1", "message": "转人工"}}
    parsed = [json.loads(line) for line in build_ndjson_lines(event, 2)]
    assert len(parsed) == 1
    assert parsed[0]["event_type"] == "run_handoff_requested"


def _run_all():
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    _run_all()
