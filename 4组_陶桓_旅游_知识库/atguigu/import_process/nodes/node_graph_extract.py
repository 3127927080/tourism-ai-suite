import json
import re
from typing import List, Dict, Any, Tuple

from atguigu.config.prompt import (
    TRIPLE_EXTRACTION_BATCH_TEMPLATE,
    TRIPLE_EXTRACTION_SYSTEM_PROMPT,
)
from atguigu.config.relation_schema import is_valid_relation
from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.config.config import LLMConfig

from atguigu.tool.logger import logger
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage


class NodeGraphExtract(NodeBase):
    """
    图关系提取节点:提取chunk块里面的三元关系组（批量提取优化版）
    """

    name = "node_graph_extract"

    def process(self, state: ImportGraphState):
        logger.info("NodeGraphExtract: 开始批量抽取实体关系三元组")
        chunks = state.get("chunks", [])
        if not chunks:
            logger.warning("chunks 为空，跳过三元组提取")
            return {"triples": []}

        # 初始化 LLM
        llm = init_chat_model(
            model=LLMConfig.llm_default_model,
            model_provider="openai",
            api_key=LLMConfig.chat_api_key,
            base_url=LLMConfig.chat_api_base,
            temperature=0.3,
        )

        # ===== 批量提取配置 =====
        BATCH_SIZE = 4  # 每批处理 4 个 chunk，可根据模型上下文窗口调整（建议 3-5）
        all_triples = []
        dropped_format = 0       # 格式不合法（不是三元组）
        dropped_relation = 0     # 关系不在 12 类词表内
        dropped_hallucination = 0  # 头尾实体未在本批正文中出现
        invalid_relations = []   # 记录被丢弃的关系，便于排查
        missing_entity_samples = []  # 记录被判定为幻觉的实体，便于排查 prompt 效果

        for batch_idx in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[batch_idx:batch_idx + BATCH_SIZE]

            # 1. 准备批次内容
            combined_parts = []
            for idx, chunk in enumerate(batch):
                content = chunk.get("content", "")
                if not content:
                    continue

                # 截断过长内容
                if len(content) > 2000:
                    content = self._truncate_by_sentence(content, 2000)

                # 给每个文本加上编号，方便 LLM 区分
                combined_parts.append(f"【文本 {idx + 1}】\n{content}")

            if not combined_parts:
                continue

            combined_content = "\n\n---\n\n".join(combined_parts)
            # 去掉空白后的正文，用于校验实体是否原样出现
            batch_text = self._normalize(combined_content)

            # 2. 构造批量提取提示词（关系词表与 relation_schema 同源）
            batch_user_prompt = TRIPLE_EXTRACTION_BATCH_TEMPLATE.format(content=combined_content)

            # 3. 调用 LLM
            try:
                messages = [
                    SystemMessage(content=TRIPLE_EXTRACTION_SYSTEM_PROMPT),
                    HumanMessage(content=batch_user_prompt)
                ]
                response = llm.invoke(messages)
                raw_output = response.content.strip()

                json_str = self._extract_json(raw_output)
                if json_str:
                    triples = json.loads(json_str)
                    if isinstance(triples, list):
                        valid_triples = []
                        for triple in triples:
                            if not (isinstance(triple, list) and len(triple) == 3):
                                dropped_format += 1
                                continue
                            head, relation, tail = (str(x).strip() for x in triple)
                            if not head or not tail:
                                dropped_format += 1
                                continue
                            # 关系必须落在 12 类词表内，否则图检索无法命中
                            if not is_valid_relation(relation):
                                dropped_relation += 1
                                if relation not in invalid_relations:
                                    invalid_relations.append(relation)
                                continue
                            # 代码兜底：头尾实体必须在本批正文中原样出现，否则视为幻觉丢弃
                            if not (self._appears_in(head, batch_text) and self._appears_in(tail, batch_text)):
                                dropped_hallucination += 1
                                for ent in (head, tail):
                                    if not self._appears_in(ent, batch_text) and ent not in missing_entity_samples:
                                        missing_entity_samples.append(ent)
                                continue
                            valid_triples.append([head, relation, tail])
                        all_triples.extend(valid_triples)
                        logger.info(
                            f"批次 {batch_idx // BATCH_SIZE + 1} 保留 {len(valid_triples)} 个三元组"
                        )
                    else:
                        logger.warning(f"批次 {batch_idx // BATCH_SIZE + 1} 返回格式不是列表: {raw_output[:100]}")
                else:
                    logger.warning(f"批次 {batch_idx // BATCH_SIZE + 1} 无法解析 JSON: {raw_output[:100]}")

            except Exception as e:
                logger.error(f"批次 {batch_idx // BATCH_SIZE + 1} 提取失败: {e}")
                continue

        # 4. 去重
        unique_triples = []
        seen = set()
        for triple in all_triples:
            key = tuple(triple)
            if key not in seen:
                seen.add(key)
                unique_triples.append(triple)

        logger.info(
            f"总共提取到 {len(unique_triples)} 个唯一三元组"
            f"（丢弃：格式异常 {dropped_format}，关系越界 {dropped_relation}，"
            f"实体不在正文 {dropped_hallucination}）"
        )
        if invalid_relations:
            logger.warning(f"被丢弃的关系词：{invalid_relations[:10]}")
        if missing_entity_samples:
            logger.warning(f"被判定不在正文的实体样例：{missing_entity_samples[:10]}")
        return {
            "triples": unique_triples
        }

    @staticmethod
    def _normalize(text: str) -> str:
        """去掉所有空白，避免换行/空格造成子串匹配失败。"""
        return re.sub(r"\s+", "", text or "")

    @classmethod
    def _appears_in(cls, entity: str, normalized_text: str) -> bool:
        """实体（去空白后）是否为正文子串。"""
        normalized = cls._normalize(entity)
        return bool(normalized) and normalized in normalized_text

    @staticmethod
    def _extract_json(raw: str) -> str:
        """
        从 LLM 响应中提取 JSON 字符串
        """
        # 1. 匹配 ```json ... ```
        pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
        match = re.search(pattern, raw)
        if match:
            return match.group(1).strip()

        # 2. 尝试直接解析 JSON
        try:
            json.loads(raw.strip())
            return raw.strip()
        except:
            pass

        # 3. 使用栈匹配最外层 [ ... ]
        stack = []
        start_idx = -1
        for i, ch in enumerate(raw):
            if ch == '[':
                if not stack:
                    start_idx = i
                stack.append(ch)
            elif ch == ']':
                if stack:
                    stack.pop()
                    if not stack and start_idx != -1:
                        return raw[start_idx:i+1]

        return raw.strip()

    @staticmethod
    def _truncate_by_sentence(text: str, max_len: int = 2000) -> str:
        """按句子边界截断文本"""
        if len(text) <= max_len:
            return text
        truncated = text[:max_len]
        for sep in ['。', '！', '？', '.\n', '!\n', '?\n']:
            pos = truncated.rfind(sep)
            if pos != -1:
                return truncated[:pos+1] + "..."
        return truncated[:max_len] + "..."