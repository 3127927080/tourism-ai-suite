#!/usr/bin/env python
"""跨项目联调验收脚本（旅游知识库 + 旅游客服 + 三元组）。

覆盖《跨项目验收清单》第四节的集成场景：
  INT-01 知识问答 → 8005 → 带引用回答
  INT-02 订单出行信息 → tourism-service
  INT-03 退款多轮槽位收集 → 页面动作
  INT-04 转人工 → 工单 → 人工回复 → 恢复 AI
  INT-06 会话状态持久化（--verify-restore 模式在重启后比对）

前置：8000/8001/8002/8005 已启动；INT-05 见 verify_triple_pipeline.py。
用法：
    python 跨项目联调验收.py                 # 跑 INT-01~04 并落证据
    python 跨项目联调验收.py --verify-restore  # 重启 CS 后比对会话恢复
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

CS = "http://127.0.0.1:8000"
TOURISM = "http://127.0.0.1:8001"
AI = "http://127.0.0.1:8002"
KB = "http://127.0.0.1:8005"
TRIPLE = "http://127.0.0.1:8006"
EVIDENCE_ROOT = Path(__file__).resolve().parent / "联调证据"
USER_ID = "user_001"
AGENT_ID = "agent_001"
ORDER_ID = "ORD_SANYA_001"


def http(method: str, url: str, body=None, token: str | None = None, timeout: int = 180):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode("utf-8", "replace")}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def port_open(host_port: str) -> bool:
    import socket

    host, port = host_port.rsplit(":", 1)
    sock = socket.socket()
    sock.settimeout(1.5)
    try:
        sock.connect((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def login(user_id: str, role: str) -> str:
    status, body = http("POST", f"{TOURISM}/api/v1/auth/demo-token",
                        {"user_id": user_id, "role": role})
    if status != 200 or not body:
        raise RuntimeError(f"登录失败 {user_id}: {status} {body}")
    return body["access_token"]


def current_conversation(token: str) -> dict:
    status, body = http("POST", f"{CS}/api/v1/conversations/current", None, token)
    if status != 200 or not body:
        raise RuntimeError(f"获取会话失败: {status} {body}")
    return body


def history(token: str) -> list:
    status, body = http("GET", f"{CS}/api/v1/chat/history?limit=100", None, token)
    return body if status == 200 and isinstance(body, list) else []


def say(token: str, text: str) -> dict | None:
    """发送一条用户消息，并等待该轮 AI 回复。"""
    base = max((m.get("sequence", 0) for m in history(token)), default=0)
    message_id = f"e2e_{uuid.uuid4().hex[:12]}"
    status, body = http("POST", f"{CS}/api/v1/chat/messages",
                        {"message_id": message_id, "type": "text", "content": {"text": text}}, token)
    if status != 200:
        return {"_send_error": f"{status} {body}"}
    deadline = time.time() + 180
    while time.time() < deadline:
        for msg in history(token):
            if msg.get("role") == "ai" and msg.get("sequence", 0) > base:
                return msg
        time.sleep(3)
    return None


def run_scenario(name: str, fn) -> dict:
    started = time.time()
    print(f"[{name}] 开始 ...", flush=True)
    try:
        passed, detail = fn()
    except Exception as exc:  # noqa: BLE001
        passed, detail = False, {"exception": f"{type(exc).__name__}: {exc}"}
    elapsed = round(time.time() - started, 1)
    print(f"[{name}] {'PASS' if passed else 'FAIL'} ({elapsed}s)", flush=True)
    return {"scenario": name, "passed": passed, "elapsed_seconds": elapsed, "detail": detail}


def text_of(msg: dict | None) -> str:
    return ((msg or {}).get("content") or {}).get("text") or ""


def scenario_int01(token: str) -> tuple[bool, dict]:
    msg = say(token, "三亚有哪些必去景点")
    text = text_of(msg)
    detail = {"question": "三亚有哪些必去景点", "reply": text,
              "reply_type": ((msg or {}).get("content") or {}).get("reply_type"),
              "has_citation": "参考来源" in text,
              "mentions_sanya": "三亚" in text}
    ok = bool(msg) and "参考来源" in text and "三亚" in text
    return ok, detail


def scenario_int02(token: str) -> tuple[bool, dict]:
    msg = say(token, f"帮我查一下订单 {ORDER_ID} 的出行信息")
    text = text_of(msg)
    detail = {"question": f"订单 {ORDER_ID} 出行信息", "reply": text,
              "mentions_order": ORDER_ID in text}
    ok = bool(msg) and ORDER_ID in text and len(text) > 20
    return ok, detail


def scenario_int03(token: str) -> tuple[bool, dict]:
    first = say(token, "我要申请退款")
    conv1 = current_conversation(token)
    task1 = conv1.get("current_task") or {}
    second = say(token, f"订单号 {ORDER_ID}，要全额退款，因为行程取消")
    action = ((second or {}).get("content") or {}).get("action") or {}
    conv2 = current_conversation(token)
    detail = {
        "turn1_reply": text_of(first),
        "turn1_task": task1,
        "turn2_reply": text_of(second),
        "turn2_action": action,
        "turn2_task": conv2.get("current_task"),
    }
    ok = (task1.get("task_type") == "refund_request"
          and bool(task1.get("missing_slots"))
          and action.get("href", "").find("refund") >= 0)
    return ok, detail


def scenario_int04(user_token: str, agent_token: str) -> tuple[bool, dict]:
    before = current_conversation(user_token)
    reply = say(user_token, "我要投诉酒店服务，请帮我转人工客服")
    mode_after = current_conversation(user_token).get("mode")

    status, handoffs = http("GET", f"{CS}/api/v1/handoffs", None, agent_token)
    handoffs = handoffs if isinstance(handoffs, list) else []
    target = next((h for h in handoffs
                   if h.get("conversation_id") == before.get("id")), None)
    detail = {"ai_reply": text_of(reply), "mode_after_transfer": mode_after,
              "handoff": target}
    if not target:
        return False, detail

    hid = target["id"]
    accept_status, _ = http("POST", f"{CS}/api/v1/handoffs/{hid}/accept", None, agent_token)
    human_text = "您好，我是客服小周，已收到您的投诉，正在为您核实。"
    reply_status, _ = http("POST", f"{CS}/api/v1/handoffs/{hid}/messages",
                           {"message_id": f"agent_{uuid.uuid4().hex[:10]}", "text": human_text},
                           agent_token)
    time.sleep(2)
    human_seen = any(m.get("role") == "human" and human_text in text_of(m)
                     for m in history(user_token))
    resolve_status, _ = http("POST", f"{CS}/api/v1/handoffs/{hid}/resolve", None, agent_token)
    time.sleep(2)
    mode_restored = current_conversation(user_token).get("mode")

    detail.update({
        "accept_http": accept_status, "reply_http": reply_status,
        "resolve_http": resolve_status, "human_message_seen": human_seen,
        "mode_after_resolve": mode_restored,
    })
    ok = (mode_after == "QUEUED" and accept_status in (200, 204)
          and reply_status in (200, 204) and human_seen
          and resolve_status in (200, 204) and mode_restored == "AI")
    return ok, detail


def snapshot(token: str) -> dict:
    conv = current_conversation(token)
    msgs = history(token)
    return {
        "conversation_id": conv.get("id"),
        "mode": conv.get("mode"),
        "current_task": conv.get("current_task"),
        "message_count": len(msgs),
        "last_sequence": max((m.get("sequence", 0) for m in msgs), default=0),
        "last_message_id": msgs[-1].get("message_id") if msgs else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-restore", action="store_true",
                        help="重启 CS 后使用：比对会话与历史是否恢复")
    parser.add_argument("--out", default=None, help="证据输出目录")
    args = parser.parse_args()

    run_dir = Path(args.out) if args.out else EVIDENCE_ROOT / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    user_token = login(USER_ID, "customer")

    if args.verify_restore:
        snap_file = EVIDENCE_ROOT / "session_snapshot.json"
        if not snap_file.exists():
            print("缺少快照文件，请先完整跑一次联调脚本", flush=True)
            return 2
        before = json.loads(snap_file.read_text(encoding="utf-8"))
        after = snapshot(user_token)
        ok = (before.get("conversation_id") == after.get("conversation_id")
              and after.get("message_count", 0) >= before.get("message_count", 0)
              and after.get("last_sequence", 0) >= before.get("last_sequence", 0))
        result = {"scenario": "INT-06 重启恢复", "passed": ok,
                  "before_restart": before, "after_restart": after}
        (run_dir / "int06_restore.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[INT-06] {'PASS' if ok else 'FAIL'}: {json.dumps(result, ensure_ascii=False)}", flush=True)
        return 0 if ok else 1

    health = {
        "customer_8000": port_open("127.0.0.1:8000"),
        "tourism_8001": port_open("127.0.0.1:8001"),
        "ai_8002": port_open("127.0.0.1:8002"),
        "kb_8005": port_open("127.0.0.1:8005"),
        "triple_8006": port_open("127.0.0.1:8006"),
    }
    print(f"服务健康: {health}", flush=True)

    agent_token = login(AGENT_ID, "agent")
    results = [
        run_scenario("INT-01 知识问答带引用", lambda: scenario_int01(user_token)),
        run_scenario("INT-02 订单出行信息", lambda: scenario_int02(user_token)),
        run_scenario("INT-03 退款多轮槽位", lambda: scenario_int03(user_token)),
        run_scenario("INT-04 转人工工单闭环", lambda: scenario_int04(user_token, agent_token)),
    ]

    snap = snapshot(user_token)
    (EVIDENCE_ROOT / "session_snapshot.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

    passed = sum(1 for r in results if r["passed"])
    summary = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "base_urls": {"cs": CS, "tourism": TOURISM, "ai": AI, "kb": KB, "triple": TRIPLE},
        "health": health,
        "user": USER_ID,
        "agent": AGENT_ID,
        "passed": passed,
        "total": len(results),
        "results": results,
        "session_snapshot": snap,
    }
    (run_dir / "evidence.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# 联调证据 {summary['timestamp']}", "",
             f"通过 {passed}/{len(results)}", "",
             "| 场景 | 结果 | 耗时(s) |", "|---|---|---|"]
    for item in results:
        lines.append(f"| {item['scenario']} | {'PASS' if item['passed'] else 'FAIL'} | {item['elapsed_seconds']} |")
    lines += ["", "## 服务健康", "", f"`{json.dumps(health, ensure_ascii=False)}`"]
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n结果: {passed}/{len(results)} 通过", flush=True)
    print(f"证据目录: {run_dir}", flush=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
