# atguigu/query_process/nodes/node_rerank.py

from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.build_graph_content import build_graph_content
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.rerank_tool import text_rerank


class NodeRerank(NodeBase):
    """
    节点功能：使用 Cross-Encoder 模型对 RRF 后的结果进行精确打分重排。
    支持合并 RRF 结果、网络搜索结果、图检索结果。
    """

    name: str = "node_rerank"

    def process(self, state: QueryGraphState):
        logger.info(f"【{self.name}】节点逻辑")

        # 1. 合并所有结果
        merged_chunks, rewritten_query = self.get_merged_chunks(state)

        # 2. 调用 Rerank 模型打分
        rerank_chunks = self.get_rerank_chunks(merged_chunks, rewritten_query)

        # 3. 断崖检测，动态截断
        final_rerank_chunks = self.cliff_detection(rerank_chunks)

        return {
            "reranked_docs": final_rerank_chunks,
        }

    def get_merged_chunks(self, state: QueryGraphState):
        """
        合并 RRF 结果、网络搜索结果、图检索结果
        """
        rrf_chunks = state.get("rrf_chunks", [])
        web_search_docs = state.get("web_search_docs", [])
        graph_results = state.get("graph_results", [])
        rewritten_query = state.get("rewritten_query", "")

        if not rewritten_query:
            logger.error("缺少改写后的问题")
            raise Exception("缺少改写后的问题")

        # ===== 安全获取 file_title =====
        file_title = "知识图谱"
        if rrf_chunks and isinstance(rrf_chunks[0], dict):
            file_title = rrf_chunks[0].get("file_title", "知识图谱")

        merged_chunks = []

        # 1. 格式化 RRF 结果
        for doc in rrf_chunks:
            if not doc:
                merged_chunks.append({
                    "title": "无标题",
                    "content": "无内容",
                    "source": "local",
                    "url": ""
                })
                continue
            merged_chunks.append({
                "title": doc.get("item_name", "") or doc.get("title", ""),
                "content": doc.get("content", "") or "无内容",
                "source": doc.get("source", "local"),
                "url": doc.get("url", "")
            })

        # 2. 格式化网络搜索结果
        for doc in web_search_docs:
            if not doc:
                merged_chunks.append({
                    "title": "无标题",
                    "content": "无内容",
                    "source": "web",
                    "url": ""
                })
                continue
            merged_chunks.append({
                "title": doc.get("title", ""),
                "content": doc.get("content", "") or "无内容",
                "source": "web",
                "url": doc.get("url", "")
            })

        # 3. 格式化图检索结果
        for doc in graph_results:
            if not doc:
                merged_chunks.append({
                    "title": "无标题",
                    "content": "无内容",
                    "source": "graph",
                    "url": ""
                })
                continue

            # ✅ 修复：添加完整的图结果格式化逻辑
            head = doc.get("head", "")
            tail = doc.get("tail", "")
            relation = doc.get("relation", "关联")

            # 如果图结果已有 content 直接用，否则用 build_graph_content 生成
            content = doc.get("content", "")
            if not content:
                content = build_graph_content(head, relation, tail)

            # 如果有 file_title，可以丰富内容
            if file_title and file_title != "知识图谱":
                content = f"根据《{file_title}》，{content}"

            merged_chunks.append({
                "title": head or "图关系",
                "content": content,
                "source": doc.get("source", "graph"),
                "url": doc.get("url", "")
            })

        logger.info(
            f"合并后共 {len(merged_chunks)} 篇文档（RRF: {len(rrf_chunks)}, 网络: {len(web_search_docs)}, 图: {len(graph_results)}）")

        return merged_chunks, rewritten_query

    def get_rerank_chunks(self, merged_chunks, rewritten_query):
        """
        调用 Rerank 模型进行精排
        """
        texts = [chunk.get("content", "") for chunk in merged_chunks]
        rerank_result = text_rerank(query=rewritten_query, texts=texts, limit=len(merged_chunks))

        for item in rerank_result:
            merged_chunks[item["index"]]["score"] = item["score"]

        rerank_chunks = sorted(merged_chunks, key=lambda x: x["score"], reverse=True)
        logger.info(json_format(rerank_chunks))
        return rerank_chunks

    def cliff_detection(self, rerank_chunks):
        """
        断崖检测，动态截断
        """
        RERANK_MAX_TOPK: int = 10
        RERANK_MIN_TOPK: int = 3
        RERANK_GAP_RATIO: float = 0.20
        RERANK_GAP_ABS: float = 0.15

        use_max_topk = min(len(rerank_chunks), RERANK_MAX_TOPK)
        use_min_topk = min(RERANK_MIN_TOPK, use_max_topk)

        for i in range(use_min_topk - 1, use_max_topk - 1):
            current_score = rerank_chunks[i]["score"]
            next_score = rerank_chunks[i + 1]["score"]
            abs_gap = abs(current_score - next_score)
            ratio_gap = abs_gap / (current_score + 1e-6)
            if ratio_gap > RERANK_GAP_RATIO or abs_gap > RERANK_GAP_ABS:
                return rerank_chunks[:i + 1]

        return rerank_chunks[:use_max_topk]


if __name__ == "__main__":
    node = NodeRerank()
    init_state = {
        "rewritten_query": "关于兄弟HAK180烫金机如何使用",
        "rrf_chunks": [
            # ... 测试数据
        ],
        "web_search_docs": [
            # ... 测试数据
        ],
        "graph_results": [
            # 图检索结果示例
            {
                "head": "HAK 180烫金机",
                "relation": "包含",
                "tail": "型号D01WD7001-00",
                "content": "HAK 180烫金机包含型号D01WD7001-00",
                "source": "graph",
                "score": 0.8
            }
        ]
    }
    res = node(init_state)
    logger.info(json_format(res))