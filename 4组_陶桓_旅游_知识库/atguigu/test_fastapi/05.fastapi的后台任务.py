import time

from fastapi import FastAPI, BackgroundTasks

app = FastAPI(
    title="测试5",
    description="测试fastapi后台任务",
    version="0.1.0",)


def test_task(n):
    print("执行后台任务")
    while n > 0:
        print("我爱你杨幂")
        n -= 1
        time.sleep(1)
    print("执行结束")


@app.get("/test_background")
def test_background(background_task:BackgroundTasks):
    background_task.add_task(test_task, 10)
    return {"message":"我爱你杨幂"}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)