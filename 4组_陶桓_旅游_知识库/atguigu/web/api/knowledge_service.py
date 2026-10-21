"""面向客服系统的旅游知识检索接口。只返回检索片段，不生成对话答案。"""

import hashlib
import json
import secrets
import uuid
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from atguigu.config.config import InternalAuthConfig, TourismConfig
from atguigu.query_process.nodes.node_item_name_confirm import NodeItemNameConfirm
from atguigu.query_process.retrieval_main_graph import RetrievalMainGraphRunner
from atguigu.tool.task_utils import create_queue, cleanup_task

app = FastAPI(
    title="旅游知识检索接口",
    description="面向客服系统的旅游知识检索",
    version="0.2.0",
)

SOURCE_LABELS = {
    "local": "知识库文档",
    "graph": "知识图谱",
}

# 客服检索会话统一前缀，避免污染用户侧会话历史
SESSION_PREFIX = "aisvc-kb-"


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, description="检索问题")
    top_k: int = Field(default=5, ge=1, le=10, description="返回条数上限")
    # 以下为可选的显式过滤条件，不传则由服务内部自动识别
    content_type: str | None = Field(default=None, description="内容类型过滤")
    city: str | None = Field(default=None, description="目的地城市过滤")
    entities: list[str] | None = Field(default=None, description="实体过滤")


class KnowledgeSearchItem(BaseModel):
    knowledge_id: str
    title: str
    content: str
    source: str
    score: float | None = None
    # 旅游元数据：用于引用与可追溯
    content_type: str | None = None
    city: str | None = None
    source_file: str | None = None
    source_path: str | None = None
    entities: list[str] | None = None
    route_name: str | None = None
    duration_days: int | None = None
    budget_ref: str | None = None
    estimated: bool | None = None


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


def parse_entities(raw) -> list[str] | None:
    """entities 可能来自 Milvus 的 JSON 字段或字符串，统一成字符串列表。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(raw, list):
        names = [str(item) for item in raw if isinstance(item, (str, int, float))]
        return names or None
    return None


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
        duration_days = doc.get("duration_days")

        items.append(
            KnowledgeSearchItem(
                knowledge_id=build_knowledge_id(content),
                title=title,
                content=content,
                source=source,
                score=float(score) if isinstance(score, int | float) else None,
                content_type=str(doc.get("content_type") or "").strip() or None,
                city=str(doc.get("city") or "").strip() or None,
                source_file=str(doc.get("source_file") or "").strip() or None,
                source_path=str(doc.get("source_path") or "").strip() or None,
                entities=parse_entities(doc.get("entities")),
                route_name=str(doc.get("route_name") or "").strip() or None,
                duration_days=int(duration_days) if isinstance(duration_days, int) else None,
                budget_ref=str(doc.get("budget_ref") or "").strip() or None,
                estimated=doc.get("estimated") if isinstance(doc.get("estimated"), bool) else None,
            )
        )
        if len(items) >= top_k:
            break
    return items


def search_travel_knowledge(
        query: str,
        top_k: int,
        content_type: str | None = None,
        city: str | None = None,
        entities: list[str] | None = None,
) -> KnowledgeSearchResponse:
    """识别目的地与内容类型后检索；识别失败降级为全局检索。"""
    task_id = str(uuid.uuid4())
    session_id = f"{SESSION_PREFIX}{uuid.uuid4()}"
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

        # 调用方显式传入的条件优先
        if city:
            state["item_names"] = [city]
        if content_type and content_type in TourismConfig.content_types:
            state["query_content_type"] = content_type
        if entities:
            state["query_entities"] = entities

        logger_hint = (
            f"检索：query={query!r} city={state.get('item_names')} "
            f"content_type={state.get('query_content_type')!r} "
            f"entities={state.get('query_entities')}"
        )
        print(logger_hint)

        result = RetrievalMainGraphRunner.create_and_run(state)
        docs = result.get("reranked_docs") or []
        items = adapt_docs(docs, top_k)
        return KnowledgeSearchResponse(
            items=items,
            message="" if items else "知识库中未找到相关内容",
        )
    finally:
        cleanup_task(task_id)


@app.post("/api/v1/knowledge/search")
def search_knowledge(
        body: KnowledgeSearchRequest,
        _: None = Depends(require_knowledge_token),
) -> KnowledgeSearchResponse:
    """检索旅游知识片段。message 只承载说明，不进入 items。"""
    query = body.query.strip()
    if not query:
        return KnowledgeSearchResponse()
    return search_travel_knowledge(
        query=query,
        top_k=body.top_k,
        content_type=body.content_type,
        city=body.city,
        entities=body.entities,
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8005)
