# atguigu/query_process/nodes/node_item_name_confirm.py
"""查询侧主体确认节点（旅游版）。

改造要点：
1. 从问题中提取槽位：cities / entities / content_type / rewritten_query；
2. 对齐 item_name 集合时返回 parent_item_name（主目的地）集合；
3. 识别失败不再返回澄清话术，改为空列表，由检索层降级为全局检索。
"""
import json
import re

from langchain.chat_models import init_chat_model

from atguigu.config.config import LLMConfig, MilvusConfig, RetrievalConfig, TourismConfig
from atguigu.config.prompt import (
    ITEM_NAME_EXTRACT_SYSTEM_PROMPT,
    ITEM_NAME_EXTRACT_TEMPLATE,
    QUERY_INTENT_CLASSIFY_SYSTEM_PROMPT,
    QUERY_INTENT_CLASSIFY_TEMPLATE,
)
from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.embedding_utils import get_embeddings
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.milvus_client_tool import create_reqs, search_hybird
from atguigu.tool.mongo_client_tool import (
    add_or_update_history,
    get_history_list,
    update_item_names_and_query,
)


class NodeItemNameConfirm(NodeBase):
    """
    节点功能：确认用户问题中的目的地城市与实体。
    """

    name: str = "node_item_name_confirm"

    # ============================================================
    # 一、历史与槽位
    # ============================================================
    def get_history_content(self, state: QueryGraphState):
        original_query = state.get("original_query", "")
        session_id = state.get("session_id", "")
        if not original_query:
            logger.error("用户输入为空")
            raise Exception("用户输入为空")
        if not session_id:
            logger.error("会话ID为空")
            raise Exception("会话ID为空")

        message_id = add_or_update_history(session_id, "user", original_query)
        history_list = get_history_list(session_id, ascending=True)
        history_content = ""
        for item in history_list:
            role = item.get("role", "")
            content = item.get("text", "")
            history_content += (role + "\n" + content + "\n")
        return history_content, message_id, original_query, session_id

    def get_query_slots(self, history_content: str, original_query: str):
        """调用 LLM 提取城市、实体、内容类型与改写后的问题。"""
        llm = init_chat_model(
            model=LLMConfig.item_model,
            model_provider="openai",
            temperature=LLMConfig.llm_default_temperature,
            api_key=LLMConfig.aliyun_api_key,
            base_url=LLMConfig.aliyun_api_base,
        )
        messages = [
            {"role": "system", "content": ITEM_NAME_EXTRACT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": ITEM_NAME_EXTRACT_TEMPLATE.format(
                    history_text=history_content, original_query=original_query
                ),
            },
        ]
        result = llm.invoke(input=messages)
        raw = str(result.content or "").strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()

        data = {}
        match = re.search(r"\{.*\}", raw, re.S)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                logger.error(f"槽位提取 JSON 解析失败：{raw[:200]}")
        else:
            logger.error(f"槽位提取未返回 JSON：{raw[:200]}")

        cities = [str(c).strip() for c in (data.get("cities") or []) if str(c).strip()]
        entities = [str(e).strip() for e in (data.get("entities") or []) if str(e).strip()]

        content_type = str(data.get("content_type") or "").strip()
        if content_type not in TourismConfig.content_types:
            content_type = ""

        rewritten_query = str(data.get("rewritten_query") or "").strip() or original_query

        logger.info(
            f"槽位提取：cities={cities} entities={entities} "
            f"content_type={content_type or '未识别'}"
        )
        return cities, entities, content_type, rewritten_query, llm

    # ============================================================
    # 二、主体对齐
    # ============================================================
    def get_final_search_item_names(self, cities: list[str]):
        """把识别到的城市/实体向量化后，在 item_name 集合里找最近邻。"""
        if not cities:
            return []
        item_names_embeddings = get_embeddings(cities)
        collection_name = MilvusConfig.item_name_collection
        final_search_item_names = []
        for idx, city in enumerate(cities):
            reqs = create_reqs(
                dense_data=[item_names_embeddings.get("dense", "")[idx]],
                sparse_data=[item_names_embeddings.get("sparse", "")[idx]],
                dense_anns_field="dense_vector",
                sparse_anns_field="sparse_vector",
                limit=10,
                dense_param={"metric_type": "COSINE"},
                sparse_param={"metric_type": "IP"},
            )
            res = search_hybird(
                collection_name=collection_name,
                reqs=reqs,
                ranker=(0.8, 0.2),
                output_fields=["item_name", "parent_item_name", "content_type"],
            )
            entity_list = res[0] if res else []
            for item in entity_list:
                entity = item.get("entity", {}) or {}
                final_search_item_names.append(
                    {
                        "original_item_name": city,
                        "search_item_name": entity.get("item_name", ""),
                        "parent_item_name": entity.get("parent_item_name", ""),
                        "content_type": entity.get("content_type", ""),
                        "score": item.get("distance"),
                    }
                )
        return final_search_item_names

    def align_item_names(self, final_search_item_names: list[dict]) -> list[str]:
        """返回 parent_item_name 去重集合；空列表表示走全局检索。"""
        confirmed = [
            item.get("parent_item_name")
            for item in final_search_item_names
            if (item.get("score") or 0) >= 0.85 and item.get("parent_item_name")
        ]
        optional = [
            item.get("parent_item_name")
            for item in final_search_item_names
            if 0.60 <= (item.get("score") or 0) < 0.85 and item.get("parent_item_name")
        ]
        parents = list(dict.fromkeys(confirmed or optional))
        if not parents:
            logger.info("主体对齐未命中，检索层将降级为全局检索")
        return parents

    def handler_history(self, answer: str, final_item_names, message_id, rewritten_query, session_id):
        if answer:
            message_id = add_or_update_history(session_id, "assistant", answer)
        history_list = get_history_list(session_id, limit=10)
        ids = [history.get("_id") for history in history_list]
        if ids:
            update_item_names_and_query(ids, final_item_names, rewritten_query)
        return message_id

    # ============================================================
    # 三、图谱开关下的权重
    # ============================================================
    def get_weight(self, history_content: str, llm, original_query: str):
        # 图谱关闭时不需要再调一次 LLM 做意图分类
        if not RetrievalConfig.enable_graph:
            return 1.0, 0.0

        messages_classify = [
            {"role": "system", "content": QUERY_INTENT_CLASSIFY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": QUERY_INTENT_CLASSIFY_TEMPLATE.format(
                    history_text=history_content, original_query=original_query
                ),
            },
        ]
        result_classify = llm.invoke(messages_classify)
        classify_raw = str(result_classify.content or "")
        classify_raw = re.sub(r"^```(?:json)?|```$", "", classify_raw, flags=re.M).strip()

        try:
            classify_data = json.loads(classify_raw)
            intent = classify_data.get("intent", "mixed")
            confidence = classify_data.get("confidence", 0.5)
        except json.JSONDecodeError:
            logger.error(f"意图分类 JSON 解析失败: {classify_raw}")
            intent = "mixed"
            confidence = 0.5

        if intent == "factual":
            vector_weight, graph_weight = 0.7, 0.3
        elif intent == "relational":
            vector_weight, graph_weight = 0.3, 0.7
        else:
            vector_weight, graph_weight = 0.5, 0.5

        if confidence < 0.6:
            vector_weight, graph_weight = 0.5, 0.5

        logger.info(
            f"分类结果: intent={intent}, confidence={confidence:.2f}, "
            f"vector_weight={vector_weight}, graph_weight={graph_weight}"
        )
        return vector_weight, graph_weight

    # ============================================================
    # 四、主流程
    # ============================================================
    def process(self, state: QueryGraphState):
        logger.info(f"【{self.name}】节点逻辑")
        history_content, message_id, original_query, session_id = self.get_history_content(state)

        cities, entities, content_type, rewritten_query, llm = self.get_query_slots(
            history_content, original_query
        )

        final_item_names: list[str] = []
        if cities:
            final_search_item_names = self.get_final_search_item_names(cities)
            final_item_names = self.align_item_names(final_search_item_names)
        else:
            logger.info("问题中未识别到目的地城市，直接全局检索")

        vector_weight, graph_weight = self.get_weight(history_content, llm, original_query)

        # 注意：不再通过 answer 触发澄清分支，answer 恒为空
        message_id = self.handler_history(
            "", final_item_names, message_id, rewritten_query, session_id
        )

        return {
            "message_id": message_id,
            "original_query": original_query,
            "answer": "",
            "item_names": final_item_names,
            "query_entities": entities,
            "query_content_type": content_type,
            "rewritten_query": rewritten_query,
            "history": get_history_list(session_id, limit=10, ascending=True),
            "vector_weight": vector_weight,
            "graph_weight": graph_weight,
        }


if __name__ == "__main__":
    init_state = {
        "session_id": "test_tourism_001",
        "original_query": "三亚有哪些必去景点？",
    }
    node_item_name_confirm = NodeItemNameConfirm()
    result = node_item_name_confirm(init_state)
    logger.info(json_format(result))
