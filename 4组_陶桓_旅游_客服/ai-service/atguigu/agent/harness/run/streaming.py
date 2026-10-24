"""把一次 Run 的最终结果转换成 NDJSON 流式行。

说明：本实现采用“最终文本分块”方式——先得到已校验的最终回复文本，
再按固定字符数分块下发 run_delta，最后下发最终结构化事件。
这样既保留两阶段确认与事实校验，又能让 Customer Service 边收边推送。
"""

import json
from collections.abc import Iterator
from typing import Any


def chunk_text(text: str, size: int) -> Iterator[str]:
    """按固定字符数切分文本。"""
    step = size if size > 0 else 3
    for start in range(0, len(text), step):
        yield text[start:start + step]


def build_ndjson_lines(event: dict[str, Any], chunk_size: int) -> Iterator[str]:
    """构建 NDJSON 文本行：run_completed 先分块下发，再下发最终事件。"""
    event_type = event.get("event_type")
    event_data = event.get("event_data") or {}

    # 1. 仅对直接完成（COMPLETED）的文本回复做分块流式
    if event_type == "run_completed":
        content = event_data.get("content") or {}
        text = content.get("text")
        if isinstance(text, str) and text:
            run_id = event_data.get("run_id")
            for index, piece in enumerate(chunk_text(text, chunk_size)):
                delta_event = {
                    "event_type": "run_delta",
                    "event_data": {
                        "run_id": run_id,
                        "index": index,
                        "delta": piece,
                    },
                }
                yield json.dumps(delta_event, ensure_ascii=False) + "\n"

    # 2. 始终在最后下发最终事件
    yield json.dumps(event, ensure_ascii=False) + "\n"
