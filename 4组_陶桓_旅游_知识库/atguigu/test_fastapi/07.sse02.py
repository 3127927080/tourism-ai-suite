"""
##### 场景2：异步任务+发送post请求
目标：后端先接收查询请求，后台处理，SSE 推送处理结果
案例2 使用post请求加异步操作
	1、前端发请求传递一个query，再传递一个session_id  两个参数到后端进行请求体接收
	2、后端写一个接口获取这两个参数，并返回给前端收到的消息，并启动task造消息
	3、前端订阅sse请求
	4、服务端需要写sse回复接口，从task当中获取自己的队列，从队列当中一个一个yield答案
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
    description="测试fastapi的sse异步流式输出",
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

    await q.put("我")
    await q.put("爱")
    await q.put("杨")
    await q.put("幂")
    await q.put("啊")
    await q.put(None)


@app.post("/sse02")
async def sse02(background_tasks: BackgroundTasks, body_params: BodyParams = Body(..., description="请求体参数")):
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
    while not queue_dict.get(session_id):
        await asyncio.sleep(3)

    q = queue_dict[session_id]
    while True:
        msg = await q.get()
        await asyncio.sleep(1)
        yield f"data:{msg}\n\n"

        if msg is None:
            break

@app.get("/stream/{session_id}")
async def stream_answer(session_id: str = Path(...,description="会话id")):
    return StreamingResponse(generate_answer(session_id),media_type="text/event-stream")



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)