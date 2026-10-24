# ruff: noqa
import json
import sys
import time
import uuid

import httpx
import psycopg


CS = "http://127.0.0.1:8000"
TOUR = "http://127.0.0.1:8001"
DSN = "postgresql://customer_service:customer_service@192.168.200.188:5432/"
FAIL = "AI 处理失败，请稍后重试。"

def token(uid):
    return httpx.post(TOUR + "/api/v1/auth/demo-token", json={"user_id": uid, "role": "customer"}, timeout=10).json()["access_token"]

def close_convs(uid):
    with psycopg.connect(DSN + "customer_service") as c:
        cur = c.cursor(); cur.execute("update conversations set mode='CLOSED',ended_at=now() where user_id=%s and mode in ('AI','QUEUED','HUMAN')", (uid,)); c.commit()

def new_conv(tok):
    return httpx.post(CS + "/api/v1/conversations/current", headers={"Authorization": f"Bearer {tok}"}, timeout=10).json()["id"]

def count_ai(conv):
    with psycopg.connect(DSN + "customer_service") as c:
        cur = c.cursor(); cur.execute("select count(*) from messages where conversation_id=%s and role='ai'", (conv,)); return cur.fetchone()[0]

def latest(conv):
    with psycopg.connect(DSN + "customer_service") as c:
        cur = c.cursor(); cur.execute("select content from messages where conversation_id=%s and role='ai' order by id desc limit 1", (conv,)); r = cur.fetchone()
    return r[0] if r else {}

def run_info(conv):
    with psycopg.connect(DSN + "ai_customer_service") as c:
        cur = c.cursor(); cur.execute("select id,state,error from agent_runs where conversation_id=%s order by started_at desc limit 1", (conv,)); row = cur.fetchone()
        if not row: return ("", [], "")
        rid, state, err = row
        cur.execute("select tool_name,success,result->>'code' from agent_tool_calls where run_id=%s order by created_at", (rid,)); tools = cur.fetchall()
    return state, tools, err

def task(conv):
    with psycopg.connect(DSN + "customer_service") as c:
        cur = c.cursor(); cur.execute("select task_state, mode from conversations where id=%s", (conv,)); ts, mode = cur.fetchone()
    ts = ts or {}; act = ts.get("active")
    return {"mode": mode, "active": act, "stack": [s.get("task_type") for s in ts.get("stack", [])], "history": [h.get("action") for h in ts.get("history", [])][-4:]}

def turn(tok, conv, text=None, obj=None, retries=3):
    for a in range(retries + 1):
        b = count_ai(conv)
        body = {"message_id": "acc_" + uuid.uuid4().hex[:10], "type": "text" if text is not None else "object",
                "content": {"text": text} if text is not None else obj}
        httpx.post(CS + "/api/v1/chat/messages", headers={"Authorization": f"Bearer {tok}"}, json=body, timeout=10)
        for _ in range(50):
            if count_ai(conv) > b: break
            time.sleep(1.5)
        content = latest(conv); t = content.get("text") or ""
        if t.strip() == FAIL and a < retries:
            continue
        return content, a + 1
    return latest(conv), retries + 1

def report(rows):
    print("\n==== ACCEPTANCE SUMMARY ====")
    ok = 0
    for cid, label, passed, detail, attempts in rows:
        ok += 1 if passed else 0
        print(f"{'PASS' if passed else 'FAIL'}  {cid:8} {label:22} script_attempts={attempts}  {detail}")
    print(f"TOTAL {ok}/{len(rows)} passed")
    # 重试与失败可视化：CS 轮次重试 + AI Run 状态分布
    try:
        with psycopg.connect(DSN + "customer_service") as c:
            cur = c.cursor(); cur.execute("select attempts, count(*) from conversation_turns group by attempts order by attempts")
            print("CS turn retries (attempts->turns):", dict(cur.fetchall()))
        with psycopg.connect(DSN + "ai_customer_service") as c:
            cur = c.cursor(); cur.execute("select state, count(*) from agent_runs group by state order by state")
            print("AI run states:", dict(cur.fetchall()))
    except Exception as exc:
        print("stats error:", exc)
    return ok, len(rows)

def group_a():
    rows = []
    tok = token("user_001")
    def case(cid, label, text=None, obj=None, check=None):
        close_convs("user_001")
        conv = new_conv(tok)
        content, att = turn(tok, conv, text=text, obj=obj)
        state, tools, err = run_info(conv)
        payload = {"content": content, "state": state, "tools": [t[0] for t in tools], "ok_tools": [t for t in tools if t[1]], "task": task(conv), "err": err}
        passed, detail = check(payload)
        rows.append((cid, label, passed, detail, att))
    case("CS-01", "产品咨询", "三亚有哪些五星级酒店",
         check=lambda p: ("search_travel_products" in p["tools"], f"tools={p['tools']}"))
    case("CS-02", "订单查询", "查一下订单 ORD_SANYA_001 现在是什么状态",
         check=lambda p: ("get_order" in p["tools"] and "ORD_SANYA_001" in json.dumps(p["content"], ensure_ascii=False), f"tools={p['tools']}"))
    case("CS-06", "出行信息", "查一下订单 ORD_YUNNAN_001 的出行信息",
         check=lambda p: ("get_trip_info" in p["tools"], f"tools={p['tools']}"))
    return report(rows)

def group_b():
    rows = []
    close_convs("user_001"); tok = token("user_001"); conv = new_conv(tok)
    c1, a1 = turn(tok, conv, text="我要退款")
    c2, a2 = turn(tok, conv, text="订单号 ORD_YUNNAN_001，原因是行程取消，退款类型选全额退款")
    tk = task(conv); act = tk["active"]
    has_action = bool((c2.get("action") or {}).get("href"))
    filled = bool(act and act.get("slots", {}).get("order_id"))
    rows.append(("CS-07/18", "退款多轮+页面引导", (has_action or filled), f"action={has_action} active={act and act.get('task_type')} slots={act and act.get('slots')}", a1 + a2))
    c3, a3 = turn(tok, conv, text="三亚有哪些景区推荐")
    tk = task(conv)
    rows.append(("CS-11", "流程切换", ("refund_request" in tk["stack"]), f"stack={tk['stack']}", a3))
    c4, a4 = turn(tok, conv, text="算了不办了")
    tk = task(conv)
    rows.append(("CS-13", "流程取消", (tk["active"] is None), f"active={tk['active']} hist={tk['history']}", a4))
    return report(rows)

def group_c():
    rows = []
    close_convs("user_002"); tok = token("user_002"); conv = new_conv(tok)
    c1, a1 = turn(tok, conv, text="查一下订单 ORD_SANYA_001")
    txt = json.dumps(c1, ensure_ascii=False)
    rows.append(("CS-29/35", "越权拦截(中文)", ("不属于" in txt or "ORDER_FORBIDDEN" in txt), f"text={c1.get('text','')[:40]}", a1))
    c2, a2 = turn(tok, conv, text="旅游产品的退改规则是怎么规定的")
    state, tools, err = run_info(conv)
    rows.append(("CS-03/04", "知识降级+平台规则", (any(t[0] == "search_knowledge" for t in tools) and "退改" in json.dumps(c2, ensure_ascii=False)), f"tools={[t[0] for t in tools]}", a2))
    c3, a3 = turn(tok, conv, obj={"type": "order", "id": "ORD_SANYA_002", "fields": {"status": "upcoming"}})
    tk = task(conv)
    filled = (tk["active"] and tk["active"].get("slots", {}).get("order_id") == "ORD_SANYA_002") or ("ORD_SANYA_002" in json.dumps(c3, ensure_ascii=False))
    rows.append(("CS-21", "对象消息填槽", bool(filled), f"active={tk['active'] and tk['active'].get('slots')}", a3))
    c4, a4 = turn(tok, conv, text="你好呀")
    rows.append(("CS-28", "闲聊/澄清兜底", bool(c4.get("text")), f"text={c4.get('text','')[:40]}", a4))
    c5, a5 = turn(tok, conv, text="请帮我转人工客服，我要投诉")
    state, tools, err = run_info(conv); tk = task(conv)
    rows.append(("CS-08/19", "转人工/工单", (state == "HANDED_OFF" or tk["mode"] == "QUEUED"), f"state={state} mode={tk['mode']}", a5))
    return report(rows)

if __name__ == "__main__":
    g = sys.argv[1] if len(sys.argv) > 1 else "a"
    {"a": group_a, "b": group_b, "c": group_c}[g]()
