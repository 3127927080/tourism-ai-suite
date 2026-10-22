"""查询链路冒烟验证（旅游知识库版）。

默认只跑检索层断言，不调 LLM、不写会话历史：
    1. 城市过滤         item_name == 城市
    2. content_type 过滤  结果全部为指定内容类型
    3. 实体过滤         json_contains(entities, ...)
    4. 两级回退         content_type / entities 过滤过严时自动放宽
    5. 全局检索         识别不到目的地时不过滤 item_name

可选追加：
    --with-rerank  跑一遍 RRF + Rerank，验证元数据在重排后没有丢失
    --with-llm     跑槽位识别（调 LLM + Mongo），验证城市/别名/失败降级

用法：
    uv run python tools/smoke_query.py
    uv run python tools/smoke_query.py --with-rerank
    uv run python tools/smoke_query.py --with-llm
    uv run python tools/smoke_query.py --all

退出码：0 表示全部通过；1 表示存在失败项（WARN 不计入退出码）。
"""
import argparse
import json
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from atguigu.config.config import MilvusConfig  # noqa: E402
from atguigu.tool.logger import logger  # noqa: E402

# 检索结果必须带回的元数据字段，缺失即视为 rerank / 检索投影有回归
REQUIRED_META = ("content_type", "city", "source_file", "source_path", "title", "content")


@dataclass
class Outcome:
    """单条用例的执行结果。"""

    name: str
    passed: bool = True
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    summary: str = ""

    def fail(self, reason: str) -> None:
        self.passed = False
        self.failures.append(reason)

    def warn(self, reason: str) -> None:
        self.warnings.append(reason)


# ============================================================
# 一、检索层断言（不依赖 LLM）
# ============================================================
def parse_entities(raw) -> list[str]:
    """entities 可能是 JSON 字符串或列表，统一成字符串列表。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if isinstance(raw, list):
        return [str(item) for item in raw if isinstance(item, (str, int, float))]
    return []


def search_once(
    name: str,
    query: str,
    item_names: list[str],
    content_type: str = "",
    entities: list[str] | None = None,
    assert_entities: bool = True,
) -> tuple[list[dict], Outcome]:
    """直接调用向量检索节点，返回结果与用例结果。"""
    from atguigu.query_process.nodes.node_search_embedding import NodeSearchEmbedding

    outcome = Outcome(name=name)
    state = {
        "original_query": query,
        "rewritten_query": query,
        "item_names": item_names,
        "query_content_type": content_type,
        "query_entities": entities or [],
    }
    # 直接调用 process，跳过 __call__ 的进度推送：
    # __call__ 会通过 put_data(task_id, ...) 写任务队列，无队列时阻塞。
    chunks = NodeSearchEmbedding().process(state).get("embedding_chunks") or []
    outcome.summary = f"{len(chunks)} 条"

    if not chunks:
        outcome.fail("检索结果为空")
        return chunks, outcome

    # 1. 元数据投影完整性
    for meta_field in REQUIRED_META:
        missing = [i for i, c in enumerate(chunks) if not str(c.get(meta_field) or "").strip()]
        if missing:
            outcome.fail(f"字段 {meta_field} 缺失（第 {missing[:5]} 条）")

    # 2. 服务端过滤条件必须对全部结果成立
    if item_names:
        bad = [c.get("city") for c in chunks if str(c.get("city") or "").strip() not in item_names]
        if bad:
            outcome.fail(f"城市过滤未生效，出现 {sorted(set(bad))[:5]}")

    if content_type:
        bad = [c.get("content_type") for c in chunks if c.get("content_type") != content_type]
        if bad:
            outcome.fail(f"content_type 过滤未生效，出现 {sorted(set(bad))[:5]}")

    if entities and assert_entities:
        expected = set(entities)
        bad = [
            c.get("title") or c.get("id")
            for c in chunks
            if not expected & set(parse_entities(c.get("entities")))
        ]
        if bad:
            outcome.fail(f"实体过滤未生效，{len(bad)} 条结果不含目标实体")

    return chunks, outcome


def run_retrieval_cases() -> list[Outcome]:
    """五类检索场景：城市 / 内容类型 / 实体 / 两级回退 / 全局。"""
    outcomes: list[Outcome] = []

    # 1. 城市过滤：三亚的景点问题
    _, outcome = search_once(
        "城市过滤：三亚景点",
        "三亚有哪些必去景点",
        item_names=["三亚"],
    )
    outcomes.append(outcome)

    # 2. content_type 过滤：三亚酒店
    _, outcome = search_once(
        "content_type 过滤：三亚酒店",
        "三亚住哪里比较好",
        item_names=["三亚"],
        content_type="酒店信息",
    )
    outcomes.append(outcome)

    # 3. 实体过滤：亚龙湾
    _, outcome = search_once(
        "实体过滤：亚龙湾",
        "亚龙湾适合什么人去",
        item_names=["三亚"],
        entities=["亚龙湾"],
    )
    outcomes.append(outcome)

    # 4. 两级回退：同时给一个不存在的实体 + 酒店类型，
    #    预期先丢弃 content_type、再丢弃 entities，最终仍返回酒店类结果；
    #    回退后结果本就不含该实体，因此这里只断言 content_type 仍受控
    _, outcome = search_once(
        "两级回退：content_type + 实体过严",
        "三亚住哪里比较好",
        item_names=["三亚"],
        content_type="酒店信息",
        entities=["不存在的实体XYZ"],
        assert_entities=False,
    )
    if not outcome.passed and "检索结果为空" in " ".join(outcome.failures):
        outcome.fail("回退链路失效：过滤过严时未放宽条件")
    outcomes.append(outcome)

    # 5. 全局检索：问题里没有目的地，item_names 为空不报错
    chunks, outcome = search_once(
        "全局检索：item_names 为空",
        "推荐一些适合秋天去的地方",
        item_names=[],
    )
    if chunks:
        cities = {str(c.get("city") or "") for c in chunks}
        outcome.summary += f"，覆盖城市 {len(cities)} 个"
    outcomes.append(outcome)

    return outcomes


# ============================================================
# 二、RRF + Rerank 元数据透传
# ============================================================
def run_rerank_case() -> Outcome:
    """验证重排阶段没有丢弃元数据（8005 引用字段依赖这里）。"""
    from atguigu.query_process.retrieval_main_graph import RetrievalMainGraphRunner
    from atguigu.tool.task_utils import cleanup_task, create_queue

    outcome = Outcome(name="RRF + Rerank 元数据透传")
    task_id = f"smoke_{uuid.uuid4().hex}"
    session_id = f"smoke_query_{uuid.uuid4().hex}"
    create_queue(task_id)
    try:
        state = {
            "task_id": task_id,
            "session_id": session_id,
            "original_query": "三亚有哪些必去景点",
            "rewritten_query": "三亚有哪些必去景点",
            "item_names": ["三亚"],
            "query_content_type": "景点介绍",
            "query_entities": [],
            "history": [],
            "vector_weight": 1.0,
            "graph_weight": 0.0,
        }
        result = RetrievalMainGraphRunner.create_and_run(state)
        docs = result.get("reranked_docs") or []
        outcome.summary = f"{len(docs)} 条"
        if not docs:
            outcome.fail("重排后无可评估文档")
            return outcome
        for meta_field in ("source_file", "content_type", "city"):
            missing = [i for i, d in enumerate(docs) if not str(d.get(meta_field) or "").strip()]
            if missing:
                outcome.fail(f"重排后字段 {meta_field} 丢失（第 {missing[:5]} 条）")
    finally:
        cleanup_task(task_id)
    return outcome


# ============================================================
# 三、槽位识别（LLM + Mongo）
# ============================================================
def run_llm_cases() -> list[Outcome]:
    """验证 §6.1 验收：城市识别、别名归并、识别失败降级。"""
    from atguigu.query_process.nodes.node_item_name_confirm import NodeItemNameConfirm
    from atguigu.tool.mongo_client_tool import clear_history

    cases = [
        # (用例名, 问题, 期望 item_names, 期望 content_type)
        ("槽位识别：三亚景点", "三亚有哪些必去景点", ["三亚"], "景点介绍"),
        ("槽位识别：别名归并（大理→云南）", "大理怎么玩", ["云南"], None),
        ("槽位识别：无目的地降级", "今天天气怎么样", [], ""),
    ]

    outcomes: list[Outcome] = []
    node = NodeItemNameConfirm()
    for name, question, expect_items, expect_type in cases:
        outcome = Outcome(name=name)
        session_id = f"smoke_slots_{uuid.uuid4().hex}"
        try:
            state = {"session_id": session_id, "original_query": question}
            # 同上：直接调用 process，避免无任务队列时 put_data 阻塞
            result = node.process(state) or {}
            item_names = result.get("item_names") or []
            content_type = result.get("query_content_type") or ""
            outcome.summary = f"item_names={item_names}, content_type={content_type or '空'}"

            if set(item_names) != set(expect_items):
                outcome.fail(f"item_names 期望 {expect_items}，实际 {item_names}")
            if expect_type is not None and content_type != expect_type:
                outcome.fail(f"content_type 期望 {expect_type!r}，实际 {content_type!r}")
            if not str(result.get("rewritten_query") or "").strip():
                outcome.fail("rewritten_query 为空")
        except Exception as exc:  # noqa: BLE001
            outcome.fail(f"调用异常：{type(exc).__name__}: {exc}")
        finally:
            try:
                clear_history(session_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("清理冒烟会话 %s 失败：%s", session_id, exc)
        outcomes.append(outcome)
    return outcomes


# ============================================================
# 四、汇总输出
# ============================================================
def print_report(outcomes: list[Outcome]) -> int:
    """打印 PASS / WARN / FAIL 表格并返回退出码。"""
    failed = [o for o in outcomes if not o.passed]
    warned = [o for o in outcomes if o.passed and o.warnings]

    print(f"\n{'=' * 72}")
    print(f"集合：{MilvusConfig.chunks_collection} / {MilvusConfig.item_name_collection}")
    print(f"{'=' * 72}")
    for outcome in outcomes:
        if not outcome.passed:
            status = "FAIL"
        elif outcome.warnings:
            status = "WARN"
        else:
            status = "PASS"
        print(f"[{status}] {outcome.name}：{outcome.summary}")
        for reason in outcome.failures:
            print(f"       ✗ {reason}")
        for reason in outcome.warnings:
            print(f"       ! {reason}")

    print(f"{'-' * 72}")
    print(f"共 {len(outcomes)} 项：通过 {len(outcomes) - len(failed)}、失败 {len(failed)}、含警告 {len(warned)}")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="旅游知识库查询链路冒烟验证")
    parser.add_argument("--with-rerank", action="store_true", help="追加 RRF + Rerank 元数据透传验证")
    parser.add_argument("--with-llm", action="store_true", help="追加槽位识别验证（调 LLM 与 Mongo）")
    parser.add_argument("--all", action="store_true", help="等价于 --with-rerank --with-llm")
    args = parser.parse_args()
    if args.all:
        args.with_rerank = True
        args.with_llm = True

    logger.info("开始检索层冒烟：集合=%s", MilvusConfig.chunks_collection)
    outcomes = run_retrieval_cases()

    if args.with_rerank:
        logger.info("追加 RRF + Rerank 验证")
        outcomes.append(run_rerank_case())

    if args.with_llm:
        logger.info("追加槽位识别验证（会产生 LLM 调用）")
        outcomes.extend(run_llm_cases())

    return print_report(outcomes)


if __name__ == "__main__":
    raise SystemExit(main())
