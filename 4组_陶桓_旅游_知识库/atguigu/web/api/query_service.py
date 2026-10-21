"""
查询阶段的api服务接口
"""
import json
import uuid

from fastapi import FastAPI, Path, Body, BackgroundTasks
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

from atguigu.query_process.query_main_graph import QueryMainGraphRunner
from atguigu.tool.mongo_client_tool import get_history_list,clear_history
from atguigu.tool.task_utils import update_task_status, TASK_STATUS_PROCESSING, get_task_info, TASK_STATUS_COMPLETED, \
    create_queue, put_data, get_data, TASK_STATUS_FAILED, cleanup_task_later

app = FastAPI(
    title="查询接口",
    description="掌柜智库项目的查询部分的api接口",
    version="0.1.0",
)


# 2. 解决跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许的源
    allow_credentials=True,  # 允许携带cookie
    allow_methods=["*"],  # 允许的请求方法
    allow_headers=["*"],  # 允许的请求头
)

#1.健康检查,用于前端是否与后端的连接
@app.get("/health")
async def health():
    return {"status": "ok"}


#2.获取session_id对应的历史记录
@app.get("/history/{session_id}")
async def get_history(session_id: str =Path(...,description="会话id")):
    """
    获取session_id对应历史记录
    :param session_id:
    :return:
    """
    history_list = get_history_list(session_id)
    history_list = [
        {
            "_id": str(item.get("_id")) if item.get("_id") is not None else "",
            "session_id": item.get("session_id", ""),
            "role":item.get("role", ""),
            "text": item.get("text", ""),
            "rewritten_query": item.get("rewritten_query", ""),
            "item_names": item.get("item_names", []),
            "ts":item.get("ts")
        }
        for item in history_list
    ]
    history_list.sort(key=lambda a: a.get("ts"))
    return {"items": history_list}

#3.删除历史记录
@app.delete("/history/{session_id}")
async def delete_history(session_id: str = Path(..., description="会话id")):
    """
    删除session_id对应的历史记录
    :param session_id:
    :return:
    """
    clear_count = clear_history(session_id)
    return {"clear_count": clear_count}


#4.写查询的接口
class QueryParams(BaseModel):
    query: str = Field(..., description="查询问题")
    session_id: str = Field(..., description="会话id")

#5.写后台任务
def run_main_graph(original_query, session_id, task_id):
    try:
        init_state={
            "session_id": session_id,
            "original_query": original_query,
            "task_id": task_id
        }
        #更新字典
        update_task_status(task_id, TASK_STATUS_PROCESSING)
        put_data(task_id,"progress",get_task_info(task_id))
        QueryMainGraphRunner.create_and_run(init_state)
        update_task_status(task_id, TASK_STATUS_COMPLETED)
    except Exception as e:
        update_task_status(task_id, TASK_STATUS_FAILED)
        # q.put({"event": "error", "data": get_task_info(task_id)})
        put_data(task_id, "error", get_task_info(task_id))
        raise e
    finally:
        # 留出时间让 SSE 消费完终态事件，再释放任务资源，避免长期运行内存增长。
        cleanup_task_later(task_id)


@app.post("/query")
async def query(background_tasks:BackgroundTasks,query_params: QueryParams = Body(...,description="查询参数")):
    """
    查询接口
    :return:
    """
    task_id = str(uuid.uuid4())
    session_id = query_params.session_id
    original_query = query_params.query
    #创建队列
    create_queue(task_id)

    background_tasks.add_task(run_main_graph, original_query, session_id, task_id)

    return {
        "message": "正在查询",
        "task_id": task_id,
        "session_id": session_id,
    }

#6.sse连接接口
@app.get("/stream/{task_id}")
def stream(task_id: str = Path(..., description="任务ID")):
    def get_task_info_stream(task_id):
        while True:
            item = get_data(task_id)
            yield f"event: {item.get('event')}\n"
            yield f"data: {json.dumps(item.get('data'), ensure_ascii=False)}\n\n"

    return StreamingResponse(
        get_task_info_stream(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )






if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8003)