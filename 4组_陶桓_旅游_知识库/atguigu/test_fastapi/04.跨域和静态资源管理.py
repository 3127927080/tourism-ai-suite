from pathlib import Path

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

app = FastAPI(
    title="测试4",
    description="测试fastapi跨域和静态资源",
    version="0.1.0",)

# 当前文件目录
BASE_DIR = Path(__file__).parent
print("当前文件目录：", BASE_DIR)
static_dir = BASE_DIR / "static"

# 挂载静态文件
# 第一个参数：所有以 /static 开头的请求都交给这个模块处理
# 第二个参数：指定静态文件的存放目录
# 第三个参数：给挂载点起个名字（路由的名字）
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

#解决跨域问题,让后端接口可以被前端访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)





@app.get("/")
async def index():
    return {"message": "Hello World"}

@app.get("/test_cors")
async def test_cors():
    return {"message":"i love you"}

@app.get("/test_background")
async def test_cors():
    return {"message": "这是后端数据", "status": "success"}



if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


