# atguigu/query_process/nodes/node_rrf.py

from atguigu.config.config import RetrievalConfig
from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger


class NodeRrf(NodeBase):
    """
    节点功能：Reciprocal Rank Fusion（倒数排名融合）
    只融合 Milvus 出来的两路结果：稠密+稀疏向量路、HyDE 路。
    图谱路与网络搜索路结构异构，不进 RRF，由 Rerank 节点统一合并。
    输出条数由 RetrievalConfig.rrf_top_n 决定。
    """

    name: str = "node_rrf"

    # RRF 常数，控制排名靠前文档的分数衰减速度
    RRF_K = 60

    def process(self, state: QueryGraphState):
        logger.info(f"【{self.name}】节点逻辑")

        # 1. 获取输入
        embedding_chunks = state.get("embedding_chunks") or []
        hyde_embedding_chunks = state.get("hyde_embedding_chunks") or []

        if not embedding_chunks and not hyde_embedding_chunks:
            logger.warning("没有向量检索结果，RRF 跳过")
            return {"rrf_chunks": []}

        # 2. 融合名额直接取配置，不再按 vector_weight 反推
        rrf_limit = RetrievalConfig.rrf_top_n
        logger.info(
            f"RRF 输入：向量 {len(embedding_chunks)} 条，HyDE {len(hyde_embedding_chunks)} 条，"
            f"输出上限 {rrf_limit} 条"
        )

        # 3. RRF 融合：两路等权
        rrf_chunks_dict = {}
        for chunks, weight in ((embedding_chunks, 1), (hyde_embedding_chunks, 1)):
            for idx, chunk in enumerate(chunks):
                chunk_id = chunk.get("id")
                if chunk_id is None:
                    # 没有 id 时用正文前 100 字符作为去重标识
                    chunk_id = chunk.get("content", "")[:100]
                    if not chunk_id:
                        continue

                # RRF 分数：weight / (k + rank)
                rrf_score = weight / (self.RRF_K + idx)

                if chunk_id in rrf_chunks_dict:
                    rrf_chunks_dict[chunk_id]["rrf_score"] += rrf_score
                else:
                    chunk_copy = chunk.copy()
                    chunk_copy["rrf_score"] = rrf_score
                    # 保留原始分数用于调试
                    chunk_copy["original_score"] = chunk.get("score", 0)
                    rrf_chunks_dict[chunk_id] = chunk_copy

        # 4. 按 RRF 分数排序并截断
        rrf_chunks = sorted(
            rrf_chunks_dict.values(),
            key=lambda x: x["rrf_score"],
            reverse=True
        )[:rrf_limit]

        logger.info(f"RRF 融合完成，去重后取前 {len(rrf_chunks)} 条")
        return {
            "rrf_chunks": rrf_chunks
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
