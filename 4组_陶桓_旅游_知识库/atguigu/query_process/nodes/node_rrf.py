# atguigu/query_process/nodes/node_rrf.py

from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger


class NodeRrf(NodeBase):
    """
    节点功能：Reciprocal Rank Fusion（倒数排名融合）
    将多路召回的结果进行加权融合排序。
    根据 vector_weight 动态调整输出条数。
    """

    name: str = "node_rrf"

    def process(self, state: QueryGraphState):
        logger.info(f"【{self.name}】节点逻辑")

        # 1. 获取输入
        embedding_chunks = state.get("embedding_chunks", [])
        hyde_embedding_chunks = state.get("hyde_embedding_chunks", [])

        if not embedding_chunks and not hyde_embedding_chunks:
            logger.warning("没有向量检索结果，RRF 跳过")
            return {"rrf_chunks": []}

        # 2. 根据权重计算 RRF 输出条数
        vector_weight = state.get("vector_weight", 0.5)
        TOTAL_RERANK = 30
        WEB_FIXED = 10
        OTHER_COUNT = TOTAL_RERANK - WEB_FIXED  # 20

        # 向量类应占数量 = 20 * vector_weight
        # 最少 2 条，最多 18 条
        rrf_limit = max(2, min(18, int(OTHER_COUNT * vector_weight)))
        logger.info(f"vector_weight={vector_weight}, RRF 输出 {rrf_limit} 条")

        # 3. RRF 融合
        weight_embedding = [
            (embedding_chunks, 1),
            (hyde_embedding_chunks, 1),
        ]

        k = 60
        rrf_chunks_dict = {}

        for chunks, weight in weight_embedding:
            for idx, chunk in enumerate(chunks):
                chunk_id = chunk.get("id")
                if chunk_id is None:
                    # 如果没有 id，用 content 的前 100 字符作为标识
                    chunk_id = chunk.get("content", "")[:100]
                    if not chunk_id:
                        continue

                # RRF 分数：weight / (k + rank)
                rrf_score = weight / (k + idx)

                if chunk_id in rrf_chunks_dict:
                    # 已存在，累加 RRF 分数
                    rrf_chunks_dict[chunk_id]["rrf_score"] += rrf_score
                else:
                    # 新文档，初始化
                    chunk_copy = chunk.copy()
                    chunk_copy["rrf_score"] = rrf_score
                    # 保留原始分数用于调试
                    chunk_copy["original_score"] = chunk.get("score", 0)
                    rrf_chunks_dict[chunk_id] = chunk_copy

        # 4. 按 RRF 分数排序
        rrf_chunks = sorted(
            rrf_chunks_dict.values(),
            key=lambda x: x["rrf_score"],
            reverse=True
        )

        # 5. 返回前 rrf_limit 条
        logger.info(f"RRF 融合完成，共 {len(rrf_chunks)} 条，取前 {rrf_limit} 条")
        return {
            "rrf_chunks": rrf_chunks[:rrf_limit]
        }



if __name__ == "__main__":
    # 旅游域演示数据：向量路两条 + HyDE 路一条（与向量路重复一条），验证 RRF 融合排序
    init_state = {
        "embedding_chunks": [
            {
                "id": 1,
                "title": "### 亚龙湾",
                "content": "### 亚龙湾\n亚龙湾更适合作为海边停留和度假场景来理解，核心价值在沙滩、海景和度假氛围。",
                "item_name": "三亚",
                "file_title": "三亚景点推荐",
                "score": 0.84,
                "source": "local",
            },
            {
                "id": 2,
                "title": "### 蜈支洲岛",
                "content": "### 蜈支洲岛\n蜈支洲岛更像完整的一日离岛体验，适合整天安排。",
                "item_name": "三亚",
                "file_title": "三亚景点推荐",
                "score": 0.82,
                "source": "local",
            },
        ],
        "hyde_embedding_chunks": [
            {
                "id": 1,
                "title": "### 亚龙湾",
                "content": "### 亚龙湾\n亚龙湾更适合作为海边停留和度假场景来理解，核心价值在沙滩、海景和度假氛围。",
                "item_name": "三亚",
                "file_title": "三亚景点推荐",
                "score": 0.80,
                "source": "local",
            },
        ],
        "vector_weight": 1.0,
    }
    # 直接调用 process：__call__ 会向任务队列推送进度，此处无队列会阻塞
    result = NodeRrf().process(init_state)
    logger.info(json_format(result))
