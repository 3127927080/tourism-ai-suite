import time
from queue import Queue

from fastapi import FastAPI, BackgroundTasks
from fastapi.params import Query
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

app = FastAPI(
    title="测试6",
    description="测试fastapi的sse流式输出",
    version="0.1.0",
)

queue_dict = {}

def send_email(session_id: str):
    """造邮件，将邮件放入对应队列"""
    # 直接从字典获取队列（此时应该已经存在）
    queue_obj = queue_dict.get(session_id)
    if queue_obj is None:
        # 如果不存在（防御性编程），可以创建一个或记录错误
        # 但正常流程下不会发生
        return

    # 模拟发送五封邮件
    for i in range(1, 6):
        time.sleep(1)  # 模拟耗时操作
        queue_obj.put(f"发送第{i}封邮件 (session: {session_id})")
    queue_obj.put(None)  # 结束标志

# 解决跨域问题
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/sse01")
async def sse01(
    background_tasks: BackgroundTasks,
    session_id: str = Query(..., description="会话id"),
):
    # 1. 先为该 session_id 创建队列并存入字典
    if session_id not in queue_dict:
        queue_dict[session_id] = Queue()
    # 2. 再添加后台任务
    background_tasks.add_task(send_email, session_id)
    return {
        "session_id": session_id,
        "message": "开始生成邮件并加入队列"
    }

@app.get("/stream")
async def stream(
    session_id: str = Query(..., description="会话id")
):
    def get_email():
        # 等待队列被创建（最多等10秒，避免死循环）
        wait_count = 0
        while session_id not in queue_dict:
            time.sleep(0.5)
            wait_count += 1
            if wait_count > 20:  # 10秒超时
                yield "data: 会话超时或不存在\n\n"
                return

        q = queue_dict[session_id]
        while True:
            email = q.get()  # 阻塞直到有数据
            yield f"data: {email}\n\n"
            if email is None:
                break
            time.sleep(0.1)  # 控制推送频率

    return StreamingResponse(get_email(), media_type="text/event-stream")

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)