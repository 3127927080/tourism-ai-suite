from fastapi import FastAPI
from fastapi.responses import JSONResponse,FileResponse,HTMLResponse,PlainTextResponse,RedirectResponse


app = FastAPI(
    title="测试3",
    description="测试fastapi返回数据的接口",
    version="0.1.0",
)


#1.第一种,测试jsonresponse
@app.get("/api/user")
async def get_user():
    # 等价于直接 return {"name": "张三", "age": 20}（FastAPI 自动转 JSONResponse）
    return JSONResponse(
        content={"name": "张三", "age": 20},
        status_code=200,  # 可选，默认 200
        headers={"X-Custom-Header": "custom-value"}  # 可选，自定义响应头
    )


#2.第二种,测试文件
@app.get("/download")
async def download():
    return FileResponse(r"C:\Users\th\Desktop\import.png",
                        media_type="image/png",
                        headers={
                            "Content-Disposition": "attachment; filename=import.png"
                        })

#3.第三种,HTMLResponse
@app.get("/hello")
async def hello(name: str = "游客"):
    html_content = f"""
    <html>
        <body>
            <h1>你好，{name}！</h1>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)

#4.第四种
@app.get("/text")
async def get_text():
    return PlainTextResponse(content="这是纯文本响应", status_code=200)

#5.第五种
@app.get("/old-path")
async def redirect_old_path():
    # 重定向到 /new-path，状态码 307 表示临时重定向
    return RedirectResponse(url="/new-path", status_code=307)

@app.get("/new-path")
async def new_path():
    return {"message": "这是新接口"}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000
    )