"""
将三元组组合成符合自然语言的句子
"""
from atguigu.config.prompt import RELATION_PATTERNS


def build_graph_content(head: str, relation: str, tail: str) -> str:
    """根据关系类型生成自然语言描述"""
    pattern = RELATION_PATTERNS.get(relation, "{head} 与 {tail} 的关系是：{relation}")
    return pattern.format(head=head, relation=relation, tail=tail)