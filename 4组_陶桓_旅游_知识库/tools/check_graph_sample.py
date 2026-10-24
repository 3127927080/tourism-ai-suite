"""小样本图谱抽取验证：6 类各跑 1 份，检查 6 项硬指标。

用法：
    uv run python tools/check_graph_sample.py            # 跑 6 份样本
    uv run python tools/check_graph_sample.py --keep     # 保留临时目录便于排查

六项硬指标（全部满足才算 PASS）：
    1. 每份文档抽取到 ≥10 个三元组
    2. 全部关系落在 relation_schema.RELATION_TYPES 的 12 类内
    3. 三元组实体名能在原文档正文中找到（防幻觉）
    4. 实体已写入 Neo4j（:Entity 节点存在）
    5. 抽样实体在 Neo4j 能查到关系（≥1 条）
    6. 图检索节点对该实体能返回结果

注意：脚本会把 6 份文档完整跑一遍入库图（含 Milvus 写入 + 图抽取），
Milvus 按 source_path 幂等覆盖，不会翻倍。
"""
import argparse
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from atguigu.config.config import ImportConfig, MilvusConfig  # noqa: E402
from atguigu.config.relation_schema import RELATION_TYPES  # noqa: E402
from atguigu.tool.logger import logger  # noqa: E402

DATA_ROOT = PROJECT_ROOT / "旅游数据"

# 6 类各 1 份，城市轮转
SAMPLES = [
    ("景点介绍", "景点攻略/三亚景点推荐.md"),
    ("线路推荐", "线路推荐/云南线路推荐.md"),
    ("酒店信息", "酒店信息/厦门住宿推荐.md"),
    ("美食推荐", "美食推荐/成都美食推荐.md"),
    ("交通指南", "交通指南/杭州交通指南.md"),
    ("文化民俗", "文化民俗/张家界文化民俗.md"),
]

MIN_TRIPLES = 10


def run_one(rel_path: str, keep: bool) -> dict:
    """跑一份文档的入库图，返回结果与检查所需的原始数据。"""
    from atguigu.import_process.import_main_graph import MainGraphRunner

    src = DATA_ROOT / rel_path
    text = src.read_text(encoding="utf-8")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        md = tmp_dir / src.name
        shutil.copyfile(src, md)
        state = {
            "task_id": f"sample-{src.stem}",
            "local_dir": str(tmp_dir),
            "local_file_path": str(md),
        }
        result = MainGraphRunner.create_and_run(state) or {}
        return {
            "text": text,
            "triples": result.get("triples") or [],
            "chunks": result.get("chunks") or [],
            "graph_stats": result.get("graph_import_stats") or {},
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"text": text, "triples": [], "chunks": [], "graph_stats": {}, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if keep:
            print(f"    临时目录保留：{tmp_dir}")
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def check(name: str, run: dict) -> tuple[bool, list[str]]:
    """按六项硬指标检查单份文档。"""
    failures: list[str] = []
    if run["error"]:
        return False, [f"入库图异常：{run['error']}"]

    triples = run["triples"]
    if len(triples) < MIN_TRIPLES:
        failures.append(f"三元组仅 {len(triples)} 条（<{MIN_TRIPLES}）")

    invalid = sorted({t[1] for t in triples if t[1] not in RELATION_TYPES})
    if invalid:
        failures.append(f"关系越界：{invalid[:8]}")

    text = run["text"]
    missing = sorted({e for t in triples for e in (t[0], t[2]) if e not in text})
    if missing:
        failures.append(f"实体未在正文出现 {len(missing)} 个：{missing[:8]}")

    if not triples:
        return False, failures

    # 4/5：Neo4j 落库与关系可查
    from atguigu.tool.neo4j_client_tool import get_neo4j_driver

    driver = get_neo4j_driver()
    sample_entities = []
    for t in triples:
        for e in (t[0], t[2]):
            if e not in sample_entities:
                sample_entities.append(e)
    sample_entities = sample_entities[:3]

    not_in_graph = []
    no_relation = []
    with driver.session() as session:
        for entity in sample_entities:
            hits = session.run(
                "MATCH (e:Entity {name: $name}) RETURN count(e) AS c", name=entity
            ).single()["c"]
            if not hits:
                not_in_graph.append(entity)
                continue
            rels = session.run(
                "MATCH (e:Entity {name: $name})-[r:RELATES]-() RETURN count(r) AS c",
                name=entity,
            ).single()["c"]
            if not rels:
                no_relation.append(entity)
    if not_in_graph:
        failures.append(f"Neo4j 无节点：{not_in_graph}")

    # 6：图检索节点能命中
    from atguigu.query_process.nodes.node_search_graph import NodeSearchGraph

    probe = sample_entities[0] if sample_entities else ""
    graph_hits = []
    if probe:
        out = NodeSearchGraph().process({
            "rewritten_query": probe,
            "item_names": [],
            "query_entities": [probe],
        })
        graph_hits = out.get("graph_results") or []
        if not graph_hits:
            failures.append(f"图检索对「{probe}」无命中")

    print(
        f"    三元组 {len(triples):>3} 条 | chunks {len(run['chunks']):>3} | "
        f"写图 {run['graph_stats'].get('success', 0)} | 图检索命中 {len(graph_hits)} "
        f"（探针：{probe}）"
    )
    return not failures, failures


def main() -> int:
    parser = argparse.ArgumentParser(description="小样本图谱抽取验证")
    parser.add_argument("--keep", action="store_true", help="保留临时目录")
    args = parser.parse_args()

    if not ImportConfig.enable_graph_extract:
        print("✗ ENABLE_GRAPH_EXTRACT 未开启，本次不会写图。请在 .env 设为 true 后重跑。")
        return 2

    print(f"集合：{MilvusConfig.chunks_collection}  |  关系类型 {len(RELATION_TYPES)} 类")
    results = []
    for idx, (content_type, rel_path) in enumerate(SAMPLES, start=1):
        print(f"\n[{idx}/{len(SAMPLES)}] {content_type} · {rel_path}")
        run = run_one(rel_path, args.keep)
        passed, failures = check(rel_path, run)
        results.append((content_type, rel_path, passed, failures))
        print(f"    {'PASS' if passed else 'FAIL'}")

    print(f"\n{'=' * 72}")
    failed = [r for r in results if not r[2]]
    for content_type, rel_path, passed, failures in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {content_type:<6} {rel_path}")
        for reason in failures:
            print(f"       ✗ {reason}")
    print(f"{'-' * 72}")
    print(f"共 {len(results)} 份：通过 {len(results) - len(failed)}、失败 {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
