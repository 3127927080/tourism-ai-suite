"""最小闭环：生成 smoke 数据集（通用抽样 + 手工旅游样本）。

产出:
  data/smoke/train.jsonl   SFT 训练（messages 格式）
  data/smoke/dev.jsonl     训练期 eval_loss（messages 格式）
  data/smoke/gold.jsonl    推理评估 gold（id/query/gold）

只用于验证「4bit QLoRA 能载入 → chat template 对齐 → 能出分」这条管道，
不代表最终数据质量。正式数据由 build_tourism_triple_data.py 生成。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent  # 三元组项目根
DATA = SCRIPT_DIR / "data"
OUT = DATA / "smoke"

SYSTEM_PROMPT = (
    "你是旅游领域信息抽取专家，请从句子中抽取实体关系三元组（头实体, 关系, 尾实体）。"
    "可选关系：位于、属于、包含、途经、推荐、适合、需要、提供、邻近、以…闻名、建议、对比。"
)

# ---- 手工旅游样本：(句子, [(头, 关系, 尾), ...])，空列表表示无三元组 ----
TOURISM_TRAIN = [
    ("亚龙湾更适合作为海边停留和度假场景来理解。", [("亚龙湾", "适合", "海边度假")]),
    ("亚龙湾适合亲子游客、情侣和第一次来三亚但不想高强度赶景点的人。", [("亚龙湾", "适合", "亲子游")]),
    ("蜈支洲岛更像完整的一日离岛体验。", [("蜈支洲岛", "适合", "一日游")]),
    ("蜈支洲岛通常更适合整天安排。", [("蜈支洲岛", "建议", "整天安排")]),
    ("天涯海角更适合半天到一天的轻观光。", [("天涯海角", "适合", "轻观光")]),
    ("天涯海角属于典型传统观光景点。", [("天涯海角", "属于", "传统观光景点")]),
    ("南山更适合对大型园区、文化景观、祈福有兴趣的人。", [("南山", "适合", "祈福")]),
    ("海棠湾更适合高端酒店、免税购物和东线活动联动。", [("海棠湾", "适合", "高端酒店")]),
    ("鹿回头更适合作为城市夜景和山海俯瞰型景点。", [("鹿回头", "适合", "城市夜景")]),
    ("西岛更偏海岛生活感、渔村氛围、慢游。", [("西岛", "以…闻名", "渔村氛围")]),
    ("亚龙湾、三亚湾和大东海都属于海湾型目的地。", [
        ("大东海", "属于", "海湾型目的地"),
        ("三亚湾", "属于", "海湾型目的地"),
    ]),
    ("蜈支洲岛是三亚最典型的离岛型景点。", [("蜈支洲岛", "属于", "三亚")]),
    ("昆大丽是云南第一次自由行里最常见的经典结构之一。", [("昆大丽", "位于", "云南")]),
    ("昆大丽的核心是完成昆明、大理、丽江三地串联。", [
        ("昆大丽", "途经", "昆明"),
        ("昆大丽", "途经", "大理"),
        ("昆大丽", "途经", "丽江"),
    ]),
    ("第一天重点通常不是深玩，而是顺利抵达、休息。", []),
    ("大理转丽江适合放在单独一天处理。", [("丽江", "建议", "单独一天")]),
    ("玉龙雪山偏向自然景观和高原体验。", [("玉龙雪山", "以…闻名", "高原体验")]),
    ("厦门高崎国际机场是主要航空入口。", [("厦门高崎国际机场", "位于", "厦门")]),
    ("厦门站更适合前往中山路、沙坡尾、曾厝垵等岛内片区。", [("厦门站", "邻近", "中山路")]),
    ("厦门地铁、公交和网约车都比较实用。", [("厦门", "提供", "地铁")]),
    ("鼓浪屿前需要提前搞清楚码头和轮渡。", [("鼓浪屿", "需要", "轮渡")]),
    ("东线更偏海湾、离岛、度假酒店和海上活动。", [("东线", "适合", "海上活动")]),
]

TOURISM_GOLD = [
    ("海棠湾更适合和高端酒店、免税购物联动理解。", [("海棠湾", "适合", "免税购物")]),
    ("三亚湾更适合作为到达、短住和看海收尾片区。", [("三亚湾", "适合", "看海")]),
    ("大东海更适合兼顾海边和城市便利。", [("大东海", "适合", "海边")]),
    ("南山和海湾类景点的节奏差异很大。", [("南山", "对比", "海湾类景点")]),
    ("西岛属于需要乘船前往的海岛型目的地。", [("西岛", "需要", "乘船")]),
    ("鹿回头更适合傍晚到夜间的景观体验。", [("鹿回头", "适合", "夜景")]),
    ("昆明最适合作为进入云南的第一站。", [("昆明", "位于", "云南")]),
    ("大理古城、洱海和丽江古城构成线路最稳定的风景骨架。", [("大理古城", "邻近", "洱海")]),
    ("厦门北站更适合高铁网络转场。", [("厦门北站", "适合", "高铁转场")]),
    ("厦门站和厦门北站是常用铁路入口。", [("厦门站", "属于", "铁路入口")]),
    ("亚龙湾的沙滩和海景酒店带很有度假氛围。", [("亚龙湾", "以…闻名", "沙滩")]),
    ("如果同行有父母或长辈，南山的优先级通常会明显上升。", []),
]


def user_text(sentence: str) -> str:
    return f"请抽取以下句子中的三元组：\n句子：{sentence}"


def answer_text(triples) -> str:
    if not triples:
        return "无三元组"
    return "\n".join(f"({h}, {r}, {t})" for h, r, t in triples)


def to_messages(sentence: str, triples) -> dict:
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text(sentence)},
        {"role": "assistant", "content": answer_text(triples)},
    ]}


def load_generic(path: Path, limit: int, seed: int) -> list[dict]:
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    random.Random(seed).shuffle(rows)
    picked = rows[:limit]
    out = []
    for r in picked:
        msgs = r.get("messages") or []
        if len(msgs) < 3:
            continue
        # 统一换成旅游 12 类 system prompt，其余保留
        out.append({"messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            msgs[1],
            msgs[2],
        ]})
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"  {path.name}: {len(rows)} 条")


def main() -> None:
    generic = load_generic(DATA / "train_triple_converted.jsonl", 200, seed=42)
    tourism_train = [to_messages(s, t) for s, t in TOURISM_TRAIN]
    train = generic + tourism_train
    random.Random(7).shuffle(train)

    dev = load_generic(DATA / "dev_triple_converted.jsonl", 30, seed=11)

    gold = []
    for i, (s, t) in enumerate(TOURISM_GOLD):
        gold.append({"id": i, "query": user_text(s), "gold": answer_text(t)})

    print(f"输出目录: {OUT}")
    write_jsonl(OUT / "train.jsonl", train)
    write_jsonl(OUT / "dev.jsonl", dev)
    write_jsonl(OUT / "gold.jsonl", gold)


if __name__ == "__main__":
    main()
