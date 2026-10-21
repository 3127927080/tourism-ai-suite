"""
##### 场景3：前端输入查询内容+事件类型区分
每个消息改为字典，里面有data,还有event，event可能是process处理中的消息，也可能是end结束的消息，event其实就是代表消息的事件类型
"""
import asyncio

from fastapi import FastAPI, BackgroundTasks, Body
from queue import Queue #同步队列
from asyncio import Queue #异步队列

from fastapi.params import Path
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

app = FastAPI(
    title="测试7",
    description="测试fastapi的sse异步流式输出改变时间类型",
    version="0.1.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class BodyParams(BaseModel):
    query: str = Field(..., description="查询内容")
    session_id: str = Field(..., description="会话id")



queue_dict = {}

# 异步队列的操作都是异步操作，所以我们需要用协程
async def make_answer(query, session_id):
    """
    异步生成回答消息并放入队列

    Args:
        query: 查询内容
        session_id: 会话ID，用于标识不同的SSE连接

    Notes:
        如果指定会话的队列不存在，则创建新队列
        依次向队列放入"我"、"爱"、"杨"、"幂"、"啊"
        最后放入None表示消息生成完毕
    """
    if not queue_dict.get(session_id):
        queue_dict[session_id] = Queue()
    q = queue_dict[session_id]

    await q.put({"event": "process", "data": "我爱杨幂"})
    await q.put({"event": "process", "data": "我爱赵丽颖"})
    await q.put({"event": "process", "data": "我爱刘诗诗"})
    await q.put({"event": "process", "data": "我爱迪丽热巴"})
    await q.put({"event": "process", "data": "我爱鞠婧祎"})
    await q.put({"event": "final", "data": "结束了"})


@app.post("/sse03")
async def sse03(background_tasks: BackgroundTasks, body_params: BodyParams = Body(..., description="请求体参数")):
    """
    SSE接口，接收POST请求并启动后台任务生成消息

    Args:
        background_tasks: FastAPI后台任务管理器
        body_params: 请求体参数，包含query和session_id

    Returns:
        dict: 确认消息，提示已开始查询信息
    """
    background_tasks.add_task(make_answer, body_params.query, body_params.session_id)

    return {"msg": "收到,开始查询信息造消息"}


async def generate_answer(session_id):
    try:
        while not queue_dict.get(session_id):
            await asyncio.sleep(3)

        q = queue_dict[session_id]
        while True:
            msg = await q.get()
            await asyncio.sleep(1)
            yield f"event:{msg.get("event")}\n"
            yield f"data:{msg.get("data")}\n\n"

            if msg is None:
                break
    except Exception as e:
        yield f"event:error\n"
        yield f"data:{e}\n\n"



@app.get("/stream/{session_id}")
async def stream_answer(session_id: str = Path(...,description="会话id")):
    return StreamingResponse(generate_answer(session_id),media_type="text/event-stream")



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)