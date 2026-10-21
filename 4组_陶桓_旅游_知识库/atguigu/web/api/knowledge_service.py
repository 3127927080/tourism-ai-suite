"""面向客服系统的商品知识检索接口。只返回检索片段，不生成对话答案。"""

import hashlib
import secrets
import uuid
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from atguigu.config.config import InternalAuthConfig
from atguigu.query_process.nodes.node_item_name_confirm import NodeItemNameConfirm
from atguigu.query_process.retrieval_main_graph import RetrievalMainGraphRunner
from atguigu.tool.task_utils import create_queue, cleanup_task

app = FastAPI(
    title="知识检索接口",
    description="面向客服系统的商品知识检索",
    version="0.1.0",
)

SOURCE_LABELS = {
    "local": "知识库文档",
    "graph": "知识图谱",
}


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, description="检索问题")
    top_k: int = Field(default=5, ge=1, le=10, description="返回条数上限")


class KnowledgeSearchItem(BaseModel):
    knowledge_id: str
    title: str
    content: str
    source: str
    score: float | None = None


class KnowledgeSearchResponse(BaseModel):
    items: list[KnowledgeSearchItem] = Field(default_factory=list)
    message: str = ""


def require_knowledge_token(
        x_internal_service_token: Annotated[
            str,
            Header(alias="X-Internal-Service-Token"),
        ] = "",
) -> None:
    """校验客服系统调用知识检索的内部令牌。"""
    expected = InternalAuthConfig.knowledge_service_token or ""
    provided = x_internal_service_token or ""
    if (
            not expected
            or not provided
            or not secrets.compare_digest(provided, expected)
    ):
        raise HTTPException(status_code=401, detail="无效的内部服务令牌")


def build_knowledge_id(content: str) -> str:
    """按正文生成稳定编号。同一正文每次结果一致，不包含排名。"""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    return f"KB_{digest}"


def adapt_docs(docs: list, top_k: int) -> list[KnowledgeSearchItem]:
    """把重排结果转换成客服系统可接收的知识条目。"""
    items: list[KnowledgeSearchItem] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        content = str(doc.get("content") or "").strip()
        if not content or content == "无内容":
            continue
        title = str(doc.get("title") or "").strip() or "无标题"
        raw_source = str(doc.get("source") or "").strip()
        source = SOURCE_LABELS.get(raw_source, raw_source or "知识库文档")
        score = doc.get("score")
        items.append(
            KnowledgeSearchItem(
                knowledge_id=build_knowledge_id(content),
                title=title,
                content=content,
                source=source,
                score=float(score) if isinstance(score, int | float) else None,
            )
        )
        if len(items) >= top_k:
            break
    return items


def search_product_knowledge(query: str, top_k: int) -> KnowledgeSearchResponse:
    """先确认商品名，确认失败只返回澄清话术，成功后再做检索。"""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    create_queue(task_id)
    state = {
        "task_id": task_id,
        "session_id": session_id,
        "original_query": query,
        "history": [],
    }
    try:
        confirm_update = NodeItemNameConfirm()(state)
        if confirm_update:
            state.update(confirm_update)
        if not state.get("item_names"):
            clarification = str(state.get("answer") or "").strip()
            return KnowledgeSearchResponse(
                items=[],
                message=clarification or "请补充要咨询的商品名称",
            )
        result = RetrievalMainGraphRunner.create_and_run(state)
        docs = result.get("reranked_docs") or []
        return KnowledgeSearchResponse(
            items=adapt_docs(docs, top_k),
            message="",
        )
    finally:
        cleanup_task(task_id)


@app.post("/api/v1/knowledge/search")
def search_knowledge(
        body: KnowledgeSearchRequest,
        _: None = Depends(require_knowledge_token),
) -> KnowledgeSearchResponse:
    """检索商品知识片段。message 只承载澄清话术，不进入 items。"""
    query = body.query.strip()
    if not query:
        return KnowledgeSearchResponse()
    return search_product_knowledge(query, body.top_k)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8005)
