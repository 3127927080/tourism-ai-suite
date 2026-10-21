"""旅游关系词表（单一事实源）。

抽取 prompt、图检索 node_search_graph、gold 标注都必须引用这里，
避免三处各写一份导致不一致。
"""

RELATION_TYPES = [
    "位于",
    "属于",
    "包含",
    "途经",
    "推荐",
    "适合",
    "需要",
    "提供",
    "邻近",
    "以…闻名",
    "建议",
    "对比",
]

RELATION_DESCRIPTIONS = {
    "位于": "景点/酒店/餐厅位于某城市或片区",
    "属于": "片区、古镇属于某城市",
    "包含": "线路包含某景点或城市",
    "途经": "线路途经某城市或节点",
    "推荐": "城市/攻略推荐某景点、美食",
    "适合": "景点/线路适合某类人群",
    "需要": "景点/线路需要预约、证件、装备等",
    "提供": "酒店/景区提供接送、寄存等服务",
    "邻近": "两个地点距离近，或靠近机场、车站",
    "以…闻名": "城市/景点以某特色、美食闻名",
    "建议": "游览建议，如单独安排一天、避开高峰",
    "对比": "同类景点之间的对比或替代关系",
}


def relations_text(separator: str = "、") -> str:
    """供 prompt 直接拼接的中文关系列表。"""
    return separator.join(RELATION_TYPES)


def is_valid_relation(relation: str) -> bool:
    return str(relation or "").strip() in RELATION_TYPES
