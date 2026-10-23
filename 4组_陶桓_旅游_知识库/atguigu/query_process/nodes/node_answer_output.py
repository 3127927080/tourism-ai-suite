# atguigu/query_process/nodes/node_answer_output.py
import re

from langchain.chat_models import init_chat_model

from atguigu.config.config import LLMConfig
from atguigu.config.prompt import ANSWER_PROMPT
from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.logger import logger
from atguigu.tool.mongo_client_tool import add_or_update_history
from atguigu.tool.task_utils import put_data


class NodeAnswerOutput(NodeBase):
    """
    节点功能: 答案生成
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_answer_output"

    def process(self, state: QueryGraphState):
        """
        节点逻辑
        :param state: 工作流状态对象
        :return: 更新后的状态对象
        """
        logger.info(f"【{self.name}】节点逻辑")
        answer = state.get("answer", "")
        task_id = state.get("task_id", "")

        if answer:
            put_data(task_id, "final", {"answer": answer})
        else:
            #准备提示词,将状态字典里面获取的数据进行拼接
            chunks, item_names, prompt, query = self.format_prompt(state)

            #第二步,调用大模型,根据提示词生成答案
            answer = self.generat_answer(answer, prompt, task_id)

            #3 追加参考来源区块（不改 SSE 协议，仍走 delta 事件）
            citation = self.build_citation_block(chunks)
            if citation:
                put_data(task_id, "delta", {"delta": citation})
                answer += citation

            #4整理图片的链接
            images = self.search_final_images(chunks)

            #5写入历史
            self.write_history(answer, images, item_names, query, state, task_id)

        return {"answer": answer}

    def build_citation_block(self, chunks: list) -> str:
        """按来源生成【参考来源】区块：本地文档、知识图谱、网络结果分别标注。

        网络结果没有 source_file，必须用标题 + URL 标注，否则网页内容被答案采用
        却不可追溯；图谱结果标注「知识图谱」，避免与本地文档混淆。
        """
        lines = []
        seen = set()
        for doc in chunks or []:
            if not isinstance(doc, dict):
                continue

            source = str(doc.get("source") or "").strip()
            meta = "·".join(
                str(x).strip()
                for x in (doc.get("content_type"), doc.get("city"))
                if str(x or "").strip()
            )

            # 1. 网络结果：标题 + URL
            if source == "web":
                title = str(doc.get("title") or "").strip() or "网络结果"
                url = str(doc.get("url") or "").strip()
                key = ("web", url or title)
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f"- 网络：{title}" + (f"（{url}）" if url else ""))
                continue

            # 2. 图谱结果：标注知识图谱，溯源文件可能为空
            if source == "graph":
                label = (
                    str(doc.get("source_file") or "").strip()
                    or str(doc.get("head") or "").strip()
                    or "图谱关系"
                )
                key = ("graph", label)
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f"- 知识图谱：{label}" + (f"（{meta}）" if meta else ""))
                continue

            # 3. 本地文档：按来源文件去重
            source_file = str(doc.get("source_file") or "").strip()
            if not source_file or ("local", source_file) in seen:
                continue
            seen.add(("local", source_file))
            lines.append(f"- {source_file}" + (f"（{meta}）" if meta else ""))

        if not lines:
            return ""
        return "\n\n【参考来源】\n" + "\n".join(lines)

    def write_history(self, answer, images, item_names, query, state,task_id):
        if answer:
            add_or_update_history(
                session_id=state.get("session_id"),
                role="assistant",
                text=answer,
                image_urls=images,
                rewritten_query=query,
                item_names=item_names,
            )
            # q.put({"event": "final", "data": {"image_urls": images}})
        put_data(task_id, "final", {"image_urls": images})

    def search_final_images(self, chunks: str):
        seen = set()  # 用于去重，避免同一张图片重复出现
        md_img_pattern = re.compile(r'!\[.*?\]\((.*?)\)')
        for i, doc in enumerate(chunks):
            # 检查 text 字段中的 Markdown 图片 (主要针对 Local Chunk)
            text = doc.get("content")
            matches = md_img_pattern.findall(text)
            for img_url in matches:
                img_url = img_url.strip()
                if img_url and img_url not in seen:
                    seen.add(img_url)
        images = list(seen)
        return images

    def generat_answer(self, answer: str, prompt: str, task_id: str) -> str:
        llm = init_chat_model(
            model=LLMConfig.llm_default_model,
            temperature=LLMConfig.llm_default_temperature,
            api_key=LLMConfig.chat_api_key,
            base_url=LLMConfig.chat_api_base,
            model_provider="openai"
        )

        messages = [
            {"role": "user", "content": prompt}
        ]

        answer = ""
        res = llm.stream(messages)
        for msg in res:
            put_data(task_id, "delta", {"delta": msg.content})
            answer += msg.content
        return answer

    def format_prompt(self, state: QueryGraphState) -> tuple[str, list, str, list[str]]:
        chunks = state.get("reranked_docs")
        chunk_content = ""
        for idx, chunk in enumerate(chunks, start=1):
            title = chunk.get("title")
            content = chunk.get("content")
            source = chunk.get("source")
            url = chunk.get("url")
            content = f"[{idx}][{source}][{title}][{url}]\n{content}\n\n"
            chunk_content += content

        history = state.get("history", [])
        history_content = ""
        for h in history:
            role = h.get("role")
            text = h.get("text")
            h_content = f"[{role}]: {text}\n\n"
            history_content += h_content
        item_names = state.get("item_names") or []
        item_name_str = ",".join(item_names)
        query = state.get("rewritten_query")
        prompt = ANSWER_PROMPT.format(
            context=chunk_content,
            history=history_content,
            item_names=item_name_str,
            question=query,
        )
        if len(prompt) >= 10000:
            prompt = prompt[:10000]
        return chunks, item_names, prompt, query
