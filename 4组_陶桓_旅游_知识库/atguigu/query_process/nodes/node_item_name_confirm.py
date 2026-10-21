# atguigu/query_process/nodes/node_item_name_confirm.py

import json

from langchain.chat_models import init_chat_model

from atguigu.config.config import LLMConfig, MilvusConfig
from atguigu.config.prompt import ITEM_NAME_EXTRACT_SYSTEM_PROMPT, ITEM_NAME_EXTRACT_TEMPLATE, \
    QUERY_INTENT_CLASSIFY_SYSTEM_PROMPT, QUERY_INTENT_CLASSIFY_TEMPLATE
from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.embedding_utils import get_embeddings
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
from atguigu.tool.milvus_client_tool import create_reqs, search_hybird
from atguigu.tool.mongo_client_tool import add_or_update_history, get_history_list, update_item_names_and_query


class NodeItemNameConfirm(NodeBase):
    """
    节点功能：确认用户问题中的核心商品名称。
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_item_name_confirm"

    def get_history_content(self, state: QueryGraphState):

        original_query = state.get("original_query", "")
        session_id = state.get("session_id", "")
        # 判断
        if not original_query:
            logger.error("用户输入为空")
            raise Exception("用户输入为空")
        if not session_id:
            logger.error("会话ID为空")
            raise Exception("会话ID为空")

        # 确定有用户提问的问题,开始保存历史记录
        message_id = add_or_update_history(session_id, "user", original_query)
        # 取最近的10条历史记录,按时间正序拼接后识别主题
        history_list = get_history_list(session_id, ascending=True)
        # 对获取的结果进行整理,将每条记录的对象和问题组合
        history_content = ""
        for item in history_list:
            role = item.get("role", "")
            content = item.get("text", "")
            history_content += (role + "\n" + content + "\n")
        return history_content, message_id, original_query, session_id

    def get_item_names(self, history_content: str, original_query: str):
        llm = init_chat_model(
            model=LLMConfig.item_model,
            model_provider="openai",
            temperature=LLMConfig.llm_default_temperature,
            api_key=LLMConfig.aliyun_api_key,
            base_url=LLMConfig.aliyun_api_base
        )
        messages = [
            {"role": "system", "content": ITEM_NAME_EXTRACT_SYSTEM_PROMPT},
            {"role": "user",
             "content": ITEM_NAME_EXTRACT_TEMPLATE.format(history_text=history_content, original_query=original_query)}
        ]
        result = llm.invoke(input=messages)
        res_json = result.content
        # 对得到的数据进行处理
        if res_json.startswith("```json"):
            res_json = res_json.replace("```json", "").replace("```", "")
        # 获取到结果后先反序列化
        dict = json.loads(res_json)
        # 获取字典里面的数据
        item_names = dict.get("item_names", [])
        rewritten_query = dict.get("rewritten_query", "")
        if item_names:
            item_names = [
                item_name.replace(" ", "").replace("\n", "").replace("\t", "")
                for item_name in item_names
            ]

        # 如果rewritten_query为空,则将original_query赋给rewritten_query
        if not rewritten_query:
            rewritten_query = original_query
        return item_names, rewritten_query,llm

    def get_final_search_item_names(self, item_names):
        item_names_embeddings = get_embeddings(item_names)
        # 准备查询向量
        collection_name = MilvusConfig.item_name_collection
        final_search_item_names = []
        for idx, item_name in enumerate(item_names):
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
                output_fields=["item_name"]
            )
            print(json_format(res))
            logger.info(json_format(res[0]))
            # 对结果进行处理
            search_item_names = [
                {
                    "original_item_name": item_name,
                    "search_item_name": item.get("entity", {}).get("item_name", ""),
                    "score": item.get("distance")
                }
                for item in res[0]
            ]
            final_search_item_names.extend(search_item_names)
        return final_search_item_names

    def align_item_names(self, answer: str, final_item_names, final_search_item_names):
        # 最终确认的名字列表
        confirm_item_names = [
            item.get("search_item_name")
            for item in final_search_item_names
            if item.get("score") >= 0.85
        ]
        # 待确认的名字列表
        option_item_names = [
            item.get("search_item_name")
            for item in final_search_item_names
            if 0.60 <= item.get("score") < 0.85
        ]
        if confirm_item_names:
            final_item_names = confirm_item_names
            answer = ""
        elif option_item_names:
            final_item_names = []
            answer = f"请确认你要咨询的商品是这些的哪一个？{",".join(option_item_names)}"
        else:
            final_item_names = []
            answer = f"对不起，我无法识别你要咨询的商品名称,请重新提问。"
        return answer, final_item_names

    def handler_history(self, answer: str, final_item_names, message_id, rewritten_query, session_id):
        if answer:
            message_id = add_or_update_history(session_id, "assistant", answer)
        # 回填数据
        history_list = get_history_list(session_id, limit=10)
        ids = [history.get("_id") for history in history_list]
        if ids:
            update_item_names_and_query(ids, final_item_names, rewritten_query)
        return message_id

    def process(self, state: QueryGraphState):
        """
        节点逻辑
        :param state: 工作流状态对象
        :return: 更新后的状态对象
        """
        logger.info(f"【{self.name}】节点逻辑")
        # 1.获取用户输入和会话id,获取客户历史消息记录
        history_content, message_id, original_query, session_id = self.get_history_content(state)

        #2.调用大模型获取对应的主题
        item_names, rewritten_query,llm = self.get_item_names(history_content, original_query)

        #3,将得到的item_name向量化,然后去milvus集合中去查找,去定义工具类
        answer = ""
        final_item_names = []
        if item_names:

            final_search_item_names = self.get_final_search_item_names(item_names)

            #4获取到含有分数和商品名字的字典列表后进行对齐
            answer, final_item_names = self.align_item_names(answer, final_item_names, final_search_item_names)
        else:
            answer = "请输入你要咨询的商品名称"

        #增加大模型判断问题类型节点
        vector_weight, graph_weight = self.get_weight(history_content, llm, original_query)
        #5.第五步,根据得到的最终名字列表和answer来处理结果
        message_id = self.handler_history(answer, final_item_names, message_id, rewritten_query, session_id)

        return {
            "message_id": message_id,
            "original_query": original_query,
            "answer": answer,
            "item_names": final_item_names,
            "rewritten_query": rewritten_query,
            "history": get_history_list(session_id, limit=10, ascending=True),
            "vector_weight": vector_weight,  # ✅ 确认有这一行
            "graph_weight": graph_weight,  # ✅ 确认有这一行
        }

    def get_weight(self, history_content: str, llm, original_query: str):
        messages_classify = [
            {"role": "system", "content": QUERY_INTENT_CLASSIFY_SYSTEM_PROMPT},
            {"role": "user", "content": QUERY_INTENT_CLASSIFY_TEMPLATE.format(
                history_text=history_content,
                original_query=original_query
            )}
        ]
        result_classify = llm.invoke(messages_classify)
        classify_raw = result_classify.content

        if classify_raw.startswith("```json"):
            classify_raw = classify_raw.replace("```json", "").replace("```", "")
        try:
            classify_data = json.loads(classify_raw)
            intent = classify_data.get("intent", "mixed")
            confidence = classify_data.get("confidence", 0.5)
        except json.JSONDecodeError:
            logger.error(f"意图分类 JSON 解析失败: {classify_raw}")
            intent = "mixed"
            confidence = 0.5

        # 根据意图分配权重（0.7 / 0.3 分配）
        if intent == "factual":
            vector_weight = 0.7
            graph_weight = 0.3
        elif intent == "relational":
            vector_weight = 0.3
            graph_weight = 0.7
        else:  # mixed 或未知
            vector_weight = 0.5
            graph_weight = 0.5

        # 如果置信度偏低，折中
        if confidence < 0.6:
            vector_weight = 0.5
            graph_weight = 0.5

        logger.info(f"分类结果: intent={intent}, confidence={confidence:.2f}, "
                    f"vector_weight={vector_weight}, graph_weight={graph_weight}")
        return vector_weight, graph_weight


if __name__ == '__main__':
    # 模拟会话历史
    session_id = "test_001"
    # add_or_update_history(session_id, "user", "咨询下烫金机。")
    # add_or_update_history(session_id, "assistant", "您好。请问是哪个型号")
    # add_or_update_history(session_id, "user", "hak180")
    # add_or_update_history(session_id, "assistant", "具体有什么问题呢？")

    # 初始化图状态
    init_state = {
        "session_id": "test_001",
        "original_query": "咋用？"
    }

    # 创建节点对象
    node_item_name_confirm = NodeItemNameConfirm()
    # 执行节点的单元测试
    result = node_item_name_confirm(init_state)
    # 将返回的图状态进行json序列化
    logger.info(json_format(result))