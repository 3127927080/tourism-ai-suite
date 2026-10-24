"""只读检查 Neo4j 图谱，并可选清空全库（重建旅游图前用）。

用法：
    uv run python tools/inspect_graph.py                      # 节点/关系计数 + 关系类型分布
    uv run python tools/inspect_graph.py --entity 亚龙湾       # 查单个实体的关系
    uv run python tools/inspect_graph.py --sample 20          # 抽样实体与关系
    uv run python tools/inspect_graph.py --wipe --yes         # 清空全库（不可恢复）

默认只读；`--wipe` 必须同时给 `--yes` 才会执行 MATCH (n) DETACH DELETE n。
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from atguigu.config.config import Neo4jConfig  # noqa: E402
from atguigu.tool.neo4j_client_tool import get_neo4j_driver  # noqa: E402


def scalar(session, cypher: str, **params) -> int:
    """执行单值聚合查询。"""
    record = session.run(cypher, **params).single()
    return int(record[0]) if record else 0


def print_counts(session) -> None:
    nodes = scalar(session, "MATCH (n) RETURN count(n)")
    rels = scalar(session, "MATCH ()-[r]->() RETURN count(r)")
    entities = scalar(session, "MATCH (n:Entity) RETURN count(n)")
    relates = scalar(session, "MATCH ()-[r:RELATES]->() RETURN count(r)")
    print(f"\n{'=' * 70}\n库：{Neo4jConfig.DATABASE}  @ {Neo4jConfig.URI}")
    print(f"  节点总数      {nodes}")
    print(f"  中 :Entity    {entities}")
    print(f"  关系总数      {rels}")
    print(f"  中 :RELATES   {relates}")
    if nodes and entities == 0:
        print("  ⚠ 没有任何 :Entity 节点，图检索一定命中为空")


def print_relation_types(session, topn: int = 20) -> None:
    rows = session.run(
        "MATCH ()-[r:RELATES]->() RETURN r.type AS type, count(*) AS c ORDER BY c DESC"
    ).data()
    if not rows:
        return
    print(f"\n  关系类型分布（{len(rows)} 类）：")
    for row in rows[:topn]:
        print(f"    {str(row['type']):<12} {row['c']}")


def print_samples(session, sample: int) -> None:
    rows = session.run(
        """
        MATCH (h:Entity)-[r:RELATES]->(t:Entity)
        RETURN h.name AS head, r.type AS relation, t.name AS tail
        LIMIT $limit
        """,
        limit=sample,
    ).data()
    if not rows:
        print("\n  没有可抽样的关系")
        return
    print(f"\n  关系样例 {len(rows)} 条：")
    for row in rows:
        print(f"    ({row['head']}) -[{row['relation']}]-> ({row['tail']})")


def print_entity(session, name: str) -> None:
    rows = session.run(
        """
        MATCH (e:Entity {name: $name})-[r:RELATES]-(other:Entity)
        RETURN other.name AS other, r.type AS relation
        LIMIT 50
        """,
        name=name,
    ).data()
    print(f"\n  实体「{name}」关系 {len(rows)} 条：")
    for row in rows:
        print(f"    {name} --[{row['relation']}]-- {row['other']}")


def print_entity_degree(session, topn: int = 15) -> None:
    rows = session.run(
        """
        MATCH (e:Entity)-[r:RELATES]-()
        RETURN e.name AS name, count(r) AS degree
        ORDER BY degree DESC
        LIMIT $limit
        """,
        limit=topn,
    ).data()
    if not rows:
        return
    print(f"\n  度数最高的实体（前 {len(rows)}）：")
    for row in rows:
        print(f"    {str(row['name']):<24} {row['degree']}")


def wipe(session) -> None:
    before = scalar(session, "MATCH (n) RETURN count(n)")
    session.run("MATCH (n) DETACH DELETE n")
    after = scalar(session, "MATCH (n) RETURN count(n)")
    print(f"\n  已清空：{before} → {after} 个节点（关系随节点删除）")


def main() -> int:
    parser = argparse.ArgumentParser(description="只读检查 Neo4j 图谱 / 清空全库")
    parser.add_argument("--entity", default="", help="查询指定实体的关系")
    parser.add_argument("--sample", type=int, default=5, help="关系样例条数，0 表示不打印")
    parser.add_argument("--wipe", action="store_true", help="清空全库（需同时给 --yes）")
    parser.add_argument("--yes", action="store_true", help="确认执行危险操作")
    args = parser.parse_args()

    driver = get_neo4j_driver()
    try:
        with driver.session(database=Neo4jConfig.DATABASE) as session:
            print_counts(session)

            if args.wipe:
                if not args.yes:
                    print("\n  ✗ --wipe 需要同时给 --yes，未执行")
                    return 2
                wipe(session)
                return 0

            print_relation_types(session)
            print_entity_degree(session)
            if args.sample > 0:
                print_samples(session, args.sample)
            if args.entity:
                print_entity(session, args.entity)
    finally:
        driver.close()
    print("\n完成。以上为只读结果。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
