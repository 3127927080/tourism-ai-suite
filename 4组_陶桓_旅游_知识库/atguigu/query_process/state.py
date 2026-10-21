# atguigu/query_process/state.py

from typing import List, Optional, Dict, Any, TypedDict

class QueryGraphState(TypedDict):
    """
    查询流程图状态
    包含整个查询流程中传递的所有数据。
    """
    task_id: str     # 任务id
    session_id: str  # 会话ID
    message_id: str  # 消息ID

    original_query: str  # 用户原始问题

    # 检索过程中的中间数据
    embedding_chunks: list  # 普通向量检索回来的切片
    hyde_embedding_chunks: list  # 已向量化的假设性问题切片
    web_search_docs: list  # 网络搜索回来的文档

    # 排序过程中的数据
    rrf_chunks: list  # RRF 融合排序后的切片
    reranked_docs: list  # 重排序后的最终 Top-K 文档

    # 生成过程中的数据
    prompt: str  # 组装好的 Prompt
    answer: str  # 最终生成的答案

    # 辅助信息
    item_names: List[str]  # 提取出的商品名称
    rewritten_query: str  # 改写后的问题
    history: list  # 历史对话记录

    query_intent: str  # 问题类型: "factual" | "relational" | "mixed"
    intent_confidence: float  # 分类置信度 0~1
    vector_weight: float  # 向量检索权重 0~1
    graph_weight: float  # 图检索权重 0~1
    graph_results: List[Dict[str, Any]]  # 图检索结果（新增）
    final_contexts: List[Dict[str, Any]]  # 最终用于生成答案的上下文（新增）
    graph_context: Optional[str]  # 图谱关系上下文（新增，用于答案生成）
    stats: Dict[str, Any]  # 统计信息（耗时、命中数等，新增）
    fused_results: List[Dict[str, Any]]  # 融合后的候选结果（新增）